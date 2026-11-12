#include "log/session_log.h"

#include <sys/stat.h>
#include <sys/types.h>

#include <chrono>
#include <cstdio>
#include <ctime>
#include <filesystem>

namespace {

std::mutex g_console_lock;
SessionLog *g_session_log = nullptr;

std::string timestamp_for_filename() {
  auto now = std::chrono::system_clock::now();
  auto micros = std::chrono::duration_cast<std::chrono::microseconds>(
                    now.time_since_epoch())
                    .count() %
                1000000;
  std::time_t seconds = std::chrono::system_clock::to_time_t(now);
  std::tm local{};
  localtime_r(&seconds, &local);
  char buf[64];
  std::snprintf(buf, sizeof(buf), "%04d%02d%02d_%02d%02d%02d_%06lld",
                local.tm_year + 1900, local.tm_mon + 1, local.tm_mday,
                local.tm_hour, local.tm_min, local.tm_sec,
                static_cast<long long>(micros));
  return buf;
}

}  // namespace

SessionLog::SessionLog(std::string path, FILE *stream)
    : path_(std::move(path)), stream_(stream) {}

std::unique_ptr<SessionLog> SessionLog::start(const std::string &root_dir,
                                              std::string *error) {
  std::error_code ec;
  std::filesystem::path log_dir = std::filesystem::path(root_dir) / "logs";
  std::filesystem::create_directories(log_dir, ec);
  if (ec) {
    if (error) *error = ec.message();
    return nullptr;
  }
  std::filesystem::path path =
      log_dir / ("python_dump_" + timestamp_for_filename() + ".log");
  FILE *stream = std::fopen(path.c_str(), "wx");
  if (stream == nullptr) {
    stream = std::fopen(path.c_str(), "w");
  }
  if (stream == nullptr) {
    if (error) *error = "cannot open session log";
    return nullptr;
  }
  return std::unique_ptr<SessionLog>(
      new SessionLog(path.string(), stream));
}

SessionLog::~SessionLog() {
  if (stream_ != nullptr) {
    std::lock_guard<std::mutex> guard(lock_);
    std::fflush(stream_);
    std::fclose(stream_);
    stream_ = nullptr;
  }
}

void SessionLog::write(const std::string &text) {
  if (text.empty() || stream_ == nullptr) return;
  std::lock_guard<std::mutex> guard(lock_);
  std::fwrite(text.data(), 1, text.size(), stream_);
  std::fflush(stream_);
}

void SessionLog::write_received_log(const std::string &message) {
  if (message.empty()) return;
  write("[LOG] " + message + "\n");
}

void SessionLog::write_console_only(const std::string &text) {
  if (text.empty()) return;
  std::lock_guard<std::mutex> guard(g_console_lock);
  std::fwrite(text.data(), 1, text.size(), stdout);
  std::fflush(stdout);
}

void SessionLog::flush() {
  if (stream_ == nullptr) return;
  std::lock_guard<std::mutex> guard(lock_);
  std::fflush(stream_);
}

namespace con {

void set_session_log(SessionLog *session_log) { g_session_log = session_log; }

void print(const std::string &text) {
  if (text.empty()) return;
  std::lock_guard<std::mutex> guard(g_console_lock);
  std::fwrite(text.data(), 1, text.size(), stdout);
  std::fflush(stdout);
  if (g_session_log != nullptr) g_session_log->write(text);
}

void print_line(const std::string &line) { print(line + "\n"); }

void print_console_only(const std::string &text) {
  if (text.empty()) return;
  std::lock_guard<std::mutex> guard(g_console_lock);
  std::fwrite(text.data(), 1, text.size(), stdout);
  std::fflush(stdout);
}

}  // namespace con
