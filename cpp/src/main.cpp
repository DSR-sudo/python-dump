// C++ replacement for python-dump's main.py + dma_core + web_radar.
#include <atomic>
#include <cctype>
#include <csignal>
#include <chrono>
#include <cstdio>
#include <filesystem>
#include <iostream>
#include <memory>
#include <optional>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include "dma_core.h"
#include "session_log.h"
#include "web_radar.h"

namespace {

std::atomic<bool> g_interrupted{false};

void handle_signal(int) { g_interrupted.store(true); }

std::string trim(const std::string &text) {
  size_t begin = 0;
  size_t end = text.size();
  while (begin < end && std::isspace(static_cast<unsigned char>(text[begin]))) ++begin;
  while (end > begin && std::isspace(static_cast<unsigned char>(text[end - 1]))) --end;
  return text.substr(begin, end - begin);
}

std::vector<std::string> split_ws(const std::string &text) {
  std::vector<std::string> parts;
  std::istringstream stream(text);
  std::string token;
  while (stream >> token) parts.push_back(token);
  return parts;
}

uint64_t read_le_u64(const std::string &data, size_t offset) {
  uint64_t value = 0;
  for (int i = 0; i < 8; ++i) {
    value |= static_cast<uint64_t>(
                 static_cast<unsigned char>(data[offset + i]))
             << (8 * i);
  }
  return value;
}

std::string hex_lower(uint64_t value) {
  char buffer[32];
  std::snprintf(buffer, sizeof(buffer), "0x%llx",
                static_cast<unsigned long long>(value));
  return buffer;
}

void print_banner() {
  con::print_line("");
  con::print_line("============================================================");
  con::print_line("  DMA plaintext UDP console / WebRadar (C++ build)");
  con::print_line("  status | watch | webradar start|stop|status | aim ...");
  con::print_line("============================================================");
  con::print_line("");
}

void print_help() {
  con::print_line("Commands:");
  con::print_line("  status                 peer/main/snapshot state");
  con::print_line("  watch                  refresh state every 2s (Ctrl-C returns)");
  con::print_line("  webradar start [PORT]  start the HTTP radar service");
  con::print_line("  webradar stop|status   stop or show the radar service");
  con::print_line("  aim <cmd>              send an AIM hot command to the driver");
  con::print_line("  stream_stats           print RWVG stream counters");
  con::print_line("  help | exit            this help / quit");
}

}  // namespace

int main(int argc, char **argv) {
  bool receiver_only = false;
  std::optional<int> auto_webradar_port;
  std::optional<std::string> root_override;
  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    if (arg == "--receiver-only") {
      receiver_only = true;
    } else if (arg == "--webradar") {
      auto_webradar_port = 34900;
    } else if (arg.rfind("--webradar=", 0) == 0) {
      try {
        auto_webradar_port = std::stoi(arg.substr(std::string("--webradar=").size()));
      } catch (...) {
        std::cerr << "invalid --webradar port\n";
        return 2;
      }
    } else if (arg.rfind("--root=", 0) == 0) {
      root_override = arg.substr(std::string("--root=").size());
    } else if (arg == "--help" || arg == "-h") {
      std::cout << "usage: dma_webradar [--receiver-only] [--webradar[=PORT]] "
                   "[--root=DIR]\n";
      return 0;
    } else {
      std::cerr << "unknown argument: " << arg << "\n";
      return 2;
    }
  }

  const std::string root_dir =
      root_override.value_or(dma::default_web_root_dir());

  std::string session_error;
  std::unique_ptr<SessionLog> session_log =
      SessionLog::start(root_dir, &session_error);
  if (session_log) {
    con::set_session_log(session_log.get());
    con::print_line("[+] Session log: " + session_log->path());
  } else {
    con::print_line("[!] Session log unavailable: " + session_error);
  }

  std::signal(SIGINT, handle_signal);
  std::signal(SIGTERM, handle_signal);

  print_banner();

  if (receiver_only) {
    try {
      dma::DMACore core(session_log.get());
      con::print_line("UDP receiver ready (Ctrl-C or SIGTERM to stop)");
      while (!g_interrupted.load()) {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
      }
      core.shutdown();
    } catch (const std::exception &error) {
      con::print_line(std::string("[-] ") + error.what());
      return 1;
    }
    return 0;
  }

  std::unique_ptr<dma::DMACore> core;
  std::unique_ptr<dma::WebRadarService> web_radar;
  try {
    core = std::make_unique<dma::DMACore>(session_log.get());
    web_radar = std::make_unique<dma::WebRadarService>(
        *core, 34900, root_override.value_or(""));
  } catch (const std::exception &error) {
    con::print_line(std::string("[-] ") + error.what());
    return 1;
  }

  if (auto_webradar_port.has_value()) {
    auto [ok, message] = web_radar->start(auto_webradar_port);
    con::print_line("WebRadar " + message);
  }

  con::print_line(
      "Console ready. status: current state; watch: live state (Ctrl-C returns).");
  con::print_line(
      "Background messages are deferred while entering a command; Enter displays them.");

  while (!g_interrupted.load()) {
    core->begin_console_input();
    con::print_console_only("\n>> ");
    std::string line;
    const bool have_line = static_cast<bool>(std::getline(std::cin, line));
    core->end_console_input();
    if (!have_line) break;
    line = trim(line);
    if (line.empty()) continue;
    const std::vector<std::string> parts = split_ws(line);
    const std::string command = parts[0];
    std::vector<std::string> args(parts.begin() + 1, parts.end());

    if (command == "help") {
      print_help();
    } else if (command == "status") {
      con::print_line(core->format_status());
    } else if (command == "watch") {
      while (!g_interrupted.exchange(false)) {
        con::print_line(core->format_status());
        for (int tick = 0; tick < 20 && !g_interrupted.load(); ++tick) {
          std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
      }
    } else if (command == "webradar") {
      const std::string action = args.empty() ? "status" : args[0];
      if (action == "status") {
        jsonx::Json status = web_radar->status();
        con::print_line("WebRadar " +
                        std::string(status.get("running").truthy() ? "running"
                                                                   : "stopped") +
                        " on 0.0.0.0:" +
                        std::to_string(status.get("port").as_int(0)) +
                        " token=" + status.get("password").as_string_or("") +
                        " pwd=" + status.get("pwd_path").as_string_or(""));
      } else if (action == "start") {
        std::optional<int> port;
        if (args.size() >= 2) {
          try {
            port = std::stoi(args[1]);
          } catch (...) {
            con::print_line("Usage: webradar <start/stop/status> [Port]");
            continue;
          }
        }
        auto [ok, message] = web_radar->start(port);
        con::print_line("WebRadar " + message);
      } else if (action == "stop") {
        auto [ok, message] = web_radar->stop();
        con::print_line("WebRadar " + message);
      } else {
        con::print_line("Usage: webradar <start/stop/status> [Port]");
      }
    } else if (command == "aim") {
      if (args.empty()) {
        con::print_line("usage: aim on|off|status | aim map <name> | "
                        "aim set <key> <value>");
      } else {
        std::string joined;
        for (size_t i = 0; i < args.size(); ++i) {
          if (i) joined += " ";
          joined += args[i];
        }
        try {
          const bool sent = core->send_aim_command("aim " + joined);
          con::print_line(sent ? "aim command sent"
                               : "no live peer: client not connected yet");
        } catch (const std::exception &error) {
          con::print_line(std::string("aim send failed: ") + error.what());
        }
      }
    } else if (command == "stream_stats") {
      con::print_line(core->get_stream_stats().dump());
    } else if (command == "cr3") {
      if (args.empty()) {
        con::print_line("usage: cr3 <pid>");
      } else {
        try {
          const uint32_t pid = static_cast<uint32_t>(std::stoul(args[0]));
          auto data = core->request_bytes(dma::pack_cr3_req(pid), 24);
          if (data && data->size() >= 24) {
            const uint64_t user = read_le_u64(*data, 0);
            const uint64_t kernel = read_le_u64(*data, 8);
            const uint64_t base = read_le_u64(*data, 16);
            con::print_line("UserDTB: " + hex_lower(user) +
                            ", KernelDTB: " + hex_lower(kernel) +
                            ", Base: " + hex_lower(base));
          } else {
            con::print_line("[-] No CR3 response.");
          }
        } catch (const std::exception &error) {
          con::print_line(std::string("[-] cr3 failed: ") + error.what());
        }
      }
    } else if (command == "cpueaxh_ping" || command == "start_data_threads" ||
               command == "stop_data_threads") {
      std::string payload;
      std::string label;
      if (command == "cpueaxh_ping") {
        payload = dma::pack_pingpong_req();
        label = "Ping";
      } else if (command == "start_data_threads") {
        payload = dma::pack_start_data_threads_req();
        label = "Start data threads";
      } else {
        payload = dma::pack_stop_data_threads_req();
        label = "Stop data threads";
      }
      try {
        auto data = core->request_bytes(payload, 8);
        auto ack = data ? dma::parse_zombie_control_ack(*data) : std::nullopt;
        con::print_line(label + " ACK: " +
                        (ack ? std::to_string(*ack) : std::string("none")));
      } catch (const std::exception &error) {
        con::print_line(label + " ACK: none (" + error.what() + ")");
      }
    } else if (command == "exit" || command == "quit") {
      con::print_line("Exiting...");
      break;
    } else {
      con::print_line("Unknown command. Type 'help'.");
    }
  }

  web_radar->stop();
  core->shutdown();
  con::set_session_log(nullptr);
  return 0;
}
