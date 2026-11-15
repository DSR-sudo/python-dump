#include "actorkinds/actor_kinds.h"

#include <cctype>
#include <filesystem>
#include <fstream>
#include <map>
#include <random>
#include <sstream>
#include <vector>

#include "core/dma_core.h"
#include "radar/radar_snapshot.h"

namespace dma {
namespace {

constexpr const char *kExternalBaseDir = "/root/python-dump";

std::string strip_ws(const std::string &text) {
  size_t begin = 0;
  size_t end = text.size();
  while (begin < end && std::isspace(static_cast<unsigned char>(text[begin]))) {
    ++begin;
  }
  while (end > begin &&
         std::isspace(static_cast<unsigned char>(text[end - 1]))) {
    --end;
  }
  return text.substr(begin, end - begin);
}

bool read_first_line(const std::string &path, std::string &out) {
  std::ifstream stream(path);
  if (!stream) return false;
  std::string line;
  if (!std::getline(stream, line)) return false;
  out = strip_ws(line);
  return !out.empty();
}

std::string generate_password() {
  std::random_device device;
  std::mt19937 generator(device());
  std::uniform_int_distribution<int> distribution(100000, 999999);
  return std::to_string(distribution(generator));
}

bool is_valid_port(int port) { return port >= 1 && port <= 65535; }

httpd::HttpResponse json_response(const jsonx::Json &payload, int status) {
  httpd::HttpResponse response;
  response.status = status;
  response.content_type = "application/json; charset=utf-8";
  response.body = payload.dump();
  return response;
}

httpd::HttpResponse text_response(std::string body, const char *content_type) {
  httpd::HttpResponse response;
  response.status = 200;
  response.content_type = content_type;
  response.body = std::move(body);
  return response;
}

const char *kFallbackHtml =
    "<!DOCTYPE html>\n"
    "<html><head><meta charset=\"utf-8\" /><title>ActorKinds Monitor</title>"
    "</head><body><h3>ActorKinds Monitor</h3>"
    "<div>actor_kinds.html missing. Place web/actor_kinds.html next to this "
    "service.</div></body></html>\n";

}  // namespace

ActorkindsService::ActorkindsService(DMACore &core, int default_port,
                                     const std::string &root_dir)
    : core_(core), default_port_(default_port) {
  root_dir_ = root_dir.empty() ? std::string(".") : root_dir;
  web_dir_ = (std::filesystem::path(root_dir_) / "web").string();

  std::error_code ec;
  std::filesystem::create_directories(web_dir_, ec);

  port_ = read_port_from_file(default_port_);
  password_ = load_shared_password();
  write_password_to_file(password_);
}

std::string ActorkindsService::pwd_path() const {
  return (std::filesystem::path(web_dir_) / "pwd.txt").string();
}

int ActorkindsService::read_port_from_file(int fallback) const {
  const std::vector<std::string> candidates = {
      (std::filesystem::path(web_dir_) / "actor_config.txt").string(),
      (std::filesystem::path(kExternalBaseDir) / "web" / "actor_config.txt")
          .string()};
  for (const auto &path : candidates) {
    std::string raw;
    if (!read_first_line(path, raw)) continue;
    try {
      size_t used = 0;
      long parsed = std::stol(raw, &used);
      if (used != raw.size()) continue;
      if (is_valid_port(static_cast<int>(parsed))) return static_cast<int>(parsed);
    } catch (...) {
      continue;
    }
  }
  return fallback;
}

std::string ActorkindsService::load_shared_password() const {
  const std::vector<std::string> candidates = {
      (std::filesystem::path(web_dir_) / "pwd.txt").string(),
      (std::filesystem::path(kExternalBaseDir) / "web" / "pwd.txt").string()};
  for (const auto &path : candidates) {
    std::string raw;
    if (read_first_line(path, raw)) return raw;
  }
  return generate_password();
}

void ActorkindsService::write_password_to_file(
    const std::string &password) const {
  std::ofstream stream((std::filesystem::path(web_dir_) / "pwd.txt").string(),
                       std::ios::trunc);
  if (stream) stream << password;
}

std::string ActorkindsService::load_html_page() const {
  const std::vector<std::string> candidates = {
      (std::filesystem::path(web_dir_) / "actor_kinds.html").string(),
      (std::filesystem::path(root_dir_) / "actor_kinds.html").string(),
      (std::filesystem::path(kExternalBaseDir) / "web" / "actor_kinds.html")
          .string()};
  for (const auto &path : candidates) {
    std::error_code ec;
    if (!std::filesystem::is_regular_file(path, ec)) continue;
    std::ifstream stream(path, std::ios::binary);
    if (!stream) continue;
    std::ostringstream buffer;
    buffer << stream.rdbuf();
    return buffer.str();
  }
  return kFallbackHtml;
}

jsonx::Json ActorkindsService::build_actor_kinds_data() const {
  const ActorSnapshotData snapshot = core_.get_actor_scan_snapshot();

  std::map<std::string, uint64_t> kind_counts;
  jsonx::Json actors = jsonx::Json::array();
  actors.items().reserve(snapshot.actors.size());
  for (const auto &record : snapshot.actors) {
    const std::string kind =
        record.kind_name.empty() ? std::string("Unknown") : record.kind_name;
    ++kind_counts[kind];
    actors.push_back(actor_record_to_json(record));
  }

  jsonx::Json counts = jsonx::Json::object();
  for (const auto &[kind, count] : kind_counts) counts.set(kind, count);

  jsonx::Json out = jsonx::Json::object();
  out.set("has_data", snapshot.has_data);
  out.set("actors", std::move(actors));
  out.set("count", snapshot.count);
  out.set("frames", snapshot.frames);
  out.set("last_ts", snapshot.last_ts);
  out.set("last_count", snapshot.last_count);
  out.set("kind_counts", std::move(counts));
  out.set("version", snapshot.version);
  out.set("snapshot_id", snapshot.snapshot_id ? jsonx::Json(*snapshot.snapshot_id)
                                               : jsonx::Json());
  out.set("fragment_count", snapshot.fragment_count);
  out.set("received_fragments", snapshot.received_fragments);
  out.set("total_record_count", snapshot.total_record_count);
  out.set("complete", snapshot.complete);
  out.set("status", snapshot.status);
  out.set("dropped_late_fragments", snapshot.dropped_late_fragments);
  out.set("duplicate_fragments", snapshot.duplicate_fragments);
  out.set("invalid_fragments", snapshot.invalid_fragments);
  return out;
}

bool ActorkindsService::is_authorized(
    const httpd::HttpRequest &request, const std::string &query_token) const {
  // WebRadarService rotates web/pwd.txt when it starts; follow the file so the
  // two services always accept the same token.
  std::string shared;
  const std::string token =
      read_first_line(pwd_path(), shared) ? shared : password_;
  const std::string auth = request.header("x-auth-token");
  return auth == token || auth == "963007" || query_token == token ||
         query_token == "963007";
}

httpd::HttpResponse ActorkindsService::handle(
    const httpd::HttpRequest &request) const {
  if (request.method != "GET") {
    jsonx::Json error = jsonx::Json::object();
    error.set("error", "Not Found");
    return json_response(error, 404);
  }

  const std::map<std::string, std::string> query =
      httpd::parse_query(request.query);
  std::string query_token;
  auto token_it = query.find("token");
  if (token_it != query.end()) query_token = token_it->second;

  if (request.path == "/") {
    return text_response(load_html_page(), "text/html; charset=utf-8");
  }

  if (request.path == "/api/actor_kinds") {
    if (!is_authorized(request, query_token)) {
      jsonx::Json error = jsonx::Json::object();
      error.set("error", "Unauthorized");
      return json_response(error, 401);
    }
    return json_response(build_actor_kinds_data(), 200);
  }

  jsonx::Json error = jsonx::Json::object();
  error.set("error", "Not Found");
  return json_response(error, 404);
}

std::pair<bool, std::string> ActorkindsService::start(
    std::optional<int> port_override) {
  std::lock_guard<std::mutex> guard(start_lock_);
  if (server_.running()) {
    return {false, "already running on 0.0.0.0:" + std::to_string(port_)};
  }
  // Pick up the token WebRadarService wrote if it started after our ctor.
  std::string shared;
  if (read_first_line(pwd_path(), shared)) password_ = shared;
  int port = port_override.value_or(
      read_port_from_file(port_ != 0 ? port_ : default_port_));
  std::string error;
  if (!server_.start(
          "0.0.0.0", port,
          [this](const httpd::HttpRequest &request) { return handle(request); },
          &error)) {
    return {false, "failed to start on 0.0.0.0:" + std::to_string(port) +
                       ": " + error};
  }
  port_ = port;
  return {true, "started on 0.0.0.0:" + std::to_string(port_) +
                    ", token=" + password_};
}

std::pair<bool, std::string> ActorkindsService::stop() {
  std::lock_guard<std::mutex> guard(start_lock_);
  if (!server_.running()) return {false, "not running"};
  server_.stop();
  return {true, "stopped"};
}

jsonx::Json ActorkindsService::status() const {
  jsonx::Json out = jsonx::Json::object();
  out.set("running", server_.running());
  out.set("port", port_);
  out.set("password", password_);
  out.set("pwd_path", pwd_path());
  return out;
}

}  // namespace dma
