#include "web/web_radar.h"

#include <sys/stat.h>
#include <unistd.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <random>
#include <regex>
#include <set>
#include <sstream>
#include <unordered_set>

namespace dma {
namespace {

constexpr int kDefaultWebRadarPort = 34900;
constexpr size_t kMaxPostBody = 16384;
constexpr size_t kMaxDecryptRecent = 120;
constexpr const char *kExternalBaseDir = "/root/python-dump";
constexpr const char *kAimDevPassword = "963007";

const char *kFallbackHtml = R"HTML(<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>WebRadar</title>
<style>
body { font-family: sans-serif; background: #111; color: #eee; margin: 0; padding: 16px; }
input, button { padding: 6px 10px; margin-right: 8px; }
pre { background: #1b1b1b; border: 1px solid #333; padding: 12px; overflow: auto; max-height: 75vh; }
</style>
</head>
<body>
<h3>WebRadar</h3>
<div>
  <input id="token" placeholder="X-Auth-Token" />
  <button onclick="refresh()">Refresh</button>
</div>
<pre id="out">waiting...</pre>
<script>
async function refresh() {
  const token = document.getElementById('token').value || localStorage.getItem('wr_token') || '';
  localStorage.setItem('wr_token', token);
  const out = document.getElementById('out');
  try {
    const r = await fetch('/api/data', { headers: { 'X-Auth-Token': token } });
    const t = await r.text();
    out.textContent = t;
  } catch (e) {
    out.textContent = String(e);
  }
}
setInterval(refresh, 700);
refresh();
</script>
</body>
</html>)HTML";

const std::unordered_set<std::string> &aim_set_keys() {
  static const std::unordered_set<std::string> keys = {
      "tick_ms", "scan_ms", "ai", "visible", "max_dist_cm", "fov_px",
      "part", "disable_y", "min_px", "max_px", "reverse_px", "key_mode",
      "suppress_side", "smooth_ms"};
  return keys;
}

const std::map<std::string, std::string> &radar_map_models() {
  static const std::map<std::string, std::string> models = {
      {"db.png", "linghaodaba"},     {"cg.png", "changgongxigu"},
      {"bks.png", "bakeshen"},       {"ht.png", "hangtianjidi"},
      {"jy.png", "chaoxijianyu"},    {"jy_1f.png", "chaoxijianyu"},
      {"jy_2f.png", "chaoxijianyu"}, {"jy_3f.png", "chaoxijianyu"},
      {"jy_4f.png", "chaoxijianyu"}, {"az3.png", "az3"},
      {"az3_in.png", "az3"},
  };
  return models;
}

bool is_space(char c) {
  return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f' || c == '\v';
}

std::string strip(std::string_view text) {
  size_t begin = 0;
  size_t end = text.size();
  while (begin < end && is_space(text[begin])) ++begin;
  while (end > begin && is_space(text[end - 1])) --end;
  return std::string(text.substr(begin, end - begin));
}

bool starts_with(std::string_view text, std::string_view prefix) {
  return text.size() >= prefix.size() && text.substr(0, prefix.size()) == prefix;
}

double now_monotonic() {
  return std::chrono::duration<double>(
             std::chrono::steady_clock::now().time_since_epoch())
      .count();
}

std::string python_str_or_empty(const jsonx::Json &value) {
  if (!value.truthy()) return "";
  switch (value.type()) {
    case jsonx::Json::Type::String: return value.as_string();
    case jsonx::Json::Type::Bool: return value.as_bool() ? "True" : "False";
    case jsonx::Json::Type::Int: return std::to_string(value.as_int());
    case jsonx::Json::Type::Double: return jsonx::dump_double(value.as_double());
    default: return "";
  }
}

bool json_to_double_strict(const jsonx::Json &value, double &out) {
  switch (value.type()) {
    case jsonx::Json::Type::Null: out = 0.0; return true;
    case jsonx::Json::Type::Bool: out = value.as_bool() ? 1.0 : 0.0; return true;
    case jsonx::Json::Type::Int: out = static_cast<double>(value.as_int()); return true;
    case jsonx::Json::Type::Double: out = value.as_double(); return true;
    case jsonx::Json::Type::String: {
      const std::string &text = value.as_string();
      if (text.empty()) return false;
      try {
        size_t used = 0;
        double parsed = std::stod(text, &used);
        while (used < text.size() && is_space(text[used])) ++used;
        if (used != text.size()) return false;
        out = parsed;
        return true;
      } catch (...) {
        return false;
      }
    }
    default: return false;
  }
}

// Python: float(value or 0.0), where invalid types raise and invalid strings
// raise ValueError. Returns false on the exceptions.
bool coordinate_or_zero(const jsonx::Json &value, double &out) {
  if (!value.truthy()) {
    out = 0.0;
    return true;
  }
  return json_to_double_strict(value, out);
}

bool coords_of(const jsonx::Json &value, std::array<double, 3> &out) {
  if (!value.is_object()) return false;
  const jsonx::Json *position = value.find("position");
  if (position == nullptr || !position->is_object()) return false;
  const char *axes[3] = {"x", "y", "z"};
  for (int i = 0; i < 3; ++i) {
    jsonx::Json axis = position->get(axes[i]);
    if (!coordinate_or_zero(axis, out[i])) return false;
  }
  return true;
}

bool entity_alive(const jsonx::Json &entity) {
  const jsonx::Json *has_health = entity.find("has_health");
  if (has_health != nullptr && has_health->is_bool() && !has_health->as_bool()) {
    return true;
  }
  jsonx::Json health = entity.get("health");
  if (!health.truthy()) return false;
  double value = 0.0;
  if (!json_to_double_strict(health, value)) return true;
  return value > 0.0;
}

std::string hex_crc32(uint32_t value) {
  char buf[16];
  std::snprintf(buf, sizeof(buf), "%08x", value);
  return buf;
}

std::string default_root_dir_impl() {
  const char *env_root = std::getenv("DMA_WEBRADAR_ROOT");
  if (env_root != nullptr && env_root[0] != '\0') return env_root;
  char exe_path[4096] = {0};
  ssize_t length = ::readlink("/proc/self/exe", exe_path, sizeof(exe_path) - 1);
  std::filesystem::path exe_dir;
  if (length > 0) {
    exe_path[length] = '\0';
    exe_dir = std::filesystem::path(exe_path).parent_path();
  }
  std::error_code ec;
  // The build tree usually places the binary in <repo>/cpp/build, so also
  // probe the two parent directories before falling back to the cwd.
  const std::vector<std::filesystem::path> exe_candidates = {
      exe_dir, exe_dir.parent_path(), exe_dir.parent_path().parent_path()};
  for (const auto &candidate : exe_candidates) {
    if (candidate.empty()) continue;
    if (std::filesystem::is_directory(candidate / "web", ec)) {
      return candidate.string();
    }
  }
  std::filesystem::path cwd = std::filesystem::current_path(ec);
  if (!ec && std::filesystem::is_directory(cwd / "web", ec)) {
    return cwd.string();
  }
  return exe_dir.empty() ? std::string(".") : exe_dir.string();
}

bool read_file(const std::string &path, std::string &out) {
  std::ifstream stream(path, std::ios::binary);
  if (!stream) return false;
  std::ostringstream buffer;
  buffer << stream.rdbuf();
  out = buffer.str();
  return true;
}

std::vector<std::string> split_whitespace(const std::string &text) {
  std::vector<std::string> parts;
  std::istringstream stream(text);
  std::string token;
  while (stream >> token) parts.push_back(token);
  return parts;
}

}  // namespace

std::string default_web_root_dir() { return default_root_dir_impl(); }

WebRadarService::WebRadarService(DMACore &core, int default_port,
                                 const std::string &root_dir)
    : core_(core), default_port_(default_port) {
  root_dir_ = root_dir.empty() ? default_web_root_dir() : root_dir;
  web_dir_ = (std::filesystem::path(root_dir_) / "web").string();

  const char *image_env = std::getenv("DMA_WEBRADAR_IMAGE_DIR");
  const std::string image_from_env =
      image_env == nullptr ? "" : strip(image_env);
  std::error_code ec;
  const std::filesystem::path external_image =
      std::filesystem::path(kExternalBaseDir) / "image";
  if (!image_from_env.empty()) {
    image_dir_ = image_from_env;
  } else if (std::filesystem::is_directory(external_image, ec)) {
    image_dir_ = external_image.string();
  } else {
    image_dir_ = (std::filesystem::path(root_dir_) / "image").string();
  }

  port_ = read_port_from_file(default_port_);
  password_ = generate_password();
  ensure_web_dir();
  write_password_to_file(password_);
}

bool WebRadarService::is_valid_port(int port) { return port >= 1 && port <= 65535; }

std::string WebRadarService::generate_password() {
  std::random_device device;
  std::mt19937 generator(device());
  std::uniform_int_distribution<int> distribution(100000, 999999);
  return std::to_string(distribution(generator));
}

void WebRadarService::ensure_web_dir() {
  std::error_code ec;
  std::filesystem::create_directories(web_dir_, ec);
}

int WebRadarService::read_port_from_file(int fallback) {
  const std::vector<std::string> candidates = {
      (std::filesystem::path(web_dir_) / "config.txt").string(),
      (std::filesystem::path(kExternalBaseDir) / "web" / "config.txt").string()};
  for (const auto &path : candidates) {
    std::ifstream stream(path);
    if (!stream) continue;
    std::string raw;
    std::getline(stream, raw);
    raw = strip(raw);
    if (raw.empty()) continue;
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

void WebRadarService::write_password_to_file(const std::string &password) {
  const std::string path =
      (std::filesystem::path(web_dir_) / "pwd.txt").string();
  std::ofstream stream(path, std::ios::trunc);
  if (stream) stream << password;
}

std::string WebRadarService::load_html_page() {
  const std::vector<std::string> candidates = {
      (std::filesystem::path(web_dir_) / "webpage.html").string(),
      (std::filesystem::path(root_dir_) / "webpage.html").string(),
      (std::filesystem::path(kExternalBaseDir) / "web" / "webpage.html").string()};
  for (const auto &path : candidates) {
    std::error_code ec;
    if (!std::filesystem::is_regular_file(path, ec)) continue;
    std::string text;
    if (read_file(path, text)) return text;
  }
  return kFallbackHtml;
}

std::optional<std::string> WebRadarService::resolve_image_path(
    const std::string &request_path) {
  std::string rel = request_path.substr(std::string("/image/").size());
  std::replace(rel.begin(), rel.end(), '\\', '/');
  std::filesystem::path normalized =
      std::filesystem::path(rel).lexically_normal();
  std::string rel_text = normalized.generic_string();
  if (rel_text.empty() || rel_text == "." || starts_with(rel_text, "..")) {
    return std::nullopt;
  }
  std::error_code ec;
  std::filesystem::path base =
      std::filesystem::absolute(std::filesystem::path(image_dir_), ec)
          .lexically_normal();
  if (ec) return std::nullopt;
  std::filesystem::path target =
      std::filesystem::absolute(base / normalized, ec).lexically_normal();
  if (ec) return std::nullopt;
  const std::string base_text = base.generic_string();
  const std::string target_text = target.generic_string();
  if (!(target_text == base_text ||
        starts_with(target_text, base_text + "/"))) {
    return std::nullopt;
  }
  ec.clear();
  if (!std::filesystem::is_regular_file(target, ec)) return std::nullopt;
  return target_text;
}

std::string WebRadarService::guess_content_type(const std::string &path) {
  std::filesystem::path file(path);
  std::string extension = file.extension().string();
  std::transform(extension.begin(), extension.end(), extension.begin(),
                 [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
  static const std::map<std::string, std::string> types = {
      {".png", "image/png"},       {".jpg", "image/jpeg"},
      {".jpeg", "image/jpeg"},     {".gif", "image/gif"},
      {".webp", "image/webp"},     {".bmp", "image/bmp"},
      {".svg", "image/svg+xml"},   {".ico", "image/x-icon"},
      {".html", "text/html"},      {".css", "text/css"},
      {".js", "text/javascript"},  {".json", "application/json"},
      {".txt", "text/plain"},
  };
  auto it = types.find(extension);
  return it == types.end() ? "application/octet-stream" : it->second;
}

// ---------------------------------------------------------------------------
// Game data payloads
// ---------------------------------------------------------------------------
jsonx::Json WebRadarService::build_game_data() {
  try {
    return core_.get_radar_snapshot();
  } catch (...) {
    jsonx::Json local = jsonx::Json::object();
    local.set("id", "local");
    local.set("team_id", 0);
    local.set("camp_id", 0);
    local.set("yaw", 0.0);
    jsonx::Json position = jsonx::Json::object();
    position.set("x", 0);
    position.set("y", 0);
    local.set("position", position);
    jsonx::Json out = jsonx::Json::object();
    out.set("local_player", local);
    out.set("entities", jsonx::Json::array());
    out.set("teammates", jsonx::Json::array());
    return out;
  }
}

std::optional<RadarDataVersion> WebRadarService::game_data_version() {
  try {
    return core_.get_radar_data_version();
  } catch (...) {
    return std::nullopt;
  }
}

jsonx::Json WebRadarService::build_game_data_cached(
    const std::optional<RadarDataVersion> &version) {
  if (version.has_value()) {
    std::lock_guard<std::mutex> guard(game_data_lock_);
    if (game_data_cached_version_ && *game_data_cached_version_ == *version &&
        has_game_data_snapshot_) {
      return game_data_snapshot_;
    }
  }
  jsonx::Json snapshot = build_game_data();
  if (version.has_value()) {
    std::lock_guard<std::mutex> guard(game_data_lock_);
    if (game_data_cached_version_ && *game_data_cached_version_ == *version &&
        has_game_data_snapshot_) {
      return game_data_snapshot_;
    }
    game_data_cached_version_ = version;
    game_data_snapshot_ = snapshot;
    has_game_data_snapshot_ = true;
    game_data_body_.clear();
    game_data_token_.clear();
  }
  return snapshot;
}

std::pair<std::string, std::string> WebRadarService::get_game_data_payload(
    bool force) {
  const std::optional<RadarDataVersion> version =
      force ? std::nullopt : game_data_version();
  if (version.has_value()) {
    std::lock_guard<std::mutex> guard(game_data_lock_);
    if (game_data_cached_version_ && *game_data_cached_version_ == *version &&
        !game_data_body_.empty()) {
      return {game_data_body_, game_data_token_};
    }
  }
  jsonx::Json snapshot = build_game_data_cached(version);
  const std::string body = snapshot.dump();
  const std::string token = hex_crc32(jsonx::crc32(body));
  if (version.has_value()) {
    std::lock_guard<std::mutex> guard(game_data_lock_);
    if (game_data_cached_version_ && *game_data_cached_version_ == *version) {
      game_data_body_ = body;
      game_data_token_ = token;
    }
  }
  return {body, token};
}

// ---------------------------------------------------------------------------
// Client subscriptions
// ---------------------------------------------------------------------------
jsonx::Json WebRadarService::normalize_subscription(const jsonx::Json &payload) {
  if (!payload.is_object()) return jsonx::Json::object();
  const bool clean = payload.get("clean").truthy();
  const bool resources = payload.get_or("resources", jsonx::Json(true)).truthy();
  const bool deadbox = payload.get_or("deadbox", jsonx::Json(true)).truthy();

  std::set<int64_t> quality_set;
  jsonx::Json raw_qualities = payload.get("qualities");
  if (raw_qualities.is_array()) {
    for (const auto &quality : raw_qualities.items()) {
      if (quality.is_int() || quality.is_double() || quality.is_bool()) {
        quality_set.insert(quality.as_int(0));
      }
    }
  } else {
    for (int quality = 0; quality < 7; ++quality) quality_set.insert(quality);
  }

  jsonx::Json ego = payload.get("ego");
  if (!ego.is_object()) ego = jsonx::Json::object();
  std::string target_key = strip(python_str_or_empty(ego.get("target_key")));
  std::string target_id = strip(python_str_or_empty(ego.get("target_id")));
  if (target_key.size() > 64) target_key.resize(64);
  if (target_id.size() > 64) target_id.resize(64);
  jsonx::Json ego_out = jsonx::Json::object();
  if (ego.get("enabled").truthy() && (!target_key.empty() || !target_id.empty())) {
    int64_t radius_cm = 10000;
    jsonx::Json raw_radius = ego.get("radius_cm");
    if (raw_radius.truthy()) radius_cm = raw_radius.as_int(10000);
    radius_cm = std::max<int64_t>(1000, std::min<int64_t>(200000, radius_cm));
    ego_out.set("target_key", target_key);
    ego_out.set("target_id", target_id);
    ego_out.set("radius_cm", radius_cm);
  }

  jsonx::Json qualities = jsonx::Json::array();
  for (int64_t quality : quality_set) qualities.push_back(quality);

  jsonx::Json out = jsonx::Json::object();
  out.set("clean", clean);
  out.set("resources", resources);
  out.set("deadbox", deadbox);
  out.set("qualities", qualities);
  out.set("ego", ego_out);
  return out;
}

uint32_t WebRadarService::subscription_signature(
    const jsonx::Json &subscription) {
  return jsonx::crc32(subscription.dump_sorted());
}

bool WebRadarService::is_default_subscription(const jsonx::Json &subscription) {
  if (subscription.empty()) return false;
  if (subscription.get("clean").truthy()) return false;
  if (!subscription.get_or("resources", jsonx::Json(true)).truthy()) return false;
  if (!subscription.get_or("deadbox", jsonx::Json(true)).truthy()) return false;
  if (subscription.get("ego").truthy()) return false;
  std::set<int64_t> qualities;
  jsonx::Json qualities_value = subscription.get("qualities");
  for (const auto &quality : qualities_value.items()) {
    if (quality.is_int() || quality.is_double()) qualities.insert(quality.as_int(0));
  }
  for (int quality = 0; quality < 7; ++quality) {
    if (!qualities.count(quality)) return false;
  }
  return true;
}

bool WebRadarService::set_client_subscription(const std::string &client_id,
                                              const jsonx::Json &payload) {
  std::string id = strip(client_id);
  if (id.size() > 64) id.resize(64);
  if (id.empty()) return false;
  jsonx::Json subscription = normalize_subscription(payload);
  std::lock_guard<std::mutex> guard(client_lock_);
  client_subs_[id] = subscription;
  client_cache_.erase(id);
  client_last_seen_[id] = now_monotonic();
  if (client_subs_.size() > kMaxClientSubs) {
    std::string oldest;
    double oldest_ts = 0.0;
    bool have_oldest = false;
    for (const auto &entry : client_last_seen_) {
      if (!have_oldest || entry.second < oldest_ts) {
        oldest = entry.first;
        oldest_ts = entry.second;
        have_oldest = true;
      }
    }
    if (!oldest.empty()) {
      client_subs_.erase(oldest);
      client_cache_.erase(oldest);
      client_last_seen_.erase(oldest);
    }
  }
  return true;
}

jsonx::Json WebRadarService::filter_radar_snapshot(
    const jsonx::Json &snapshot, const jsonx::Json &subscription) {
  if (!snapshot.is_object() || !subscription.truthy()) return snapshot;
  const bool clean = subscription.get("clean").truthy();
  const bool resources = subscription.get_or("resources", jsonx::Json(true)).truthy();
  const bool deadbox = subscription.get_or("deadbox", jsonx::Json(true)).truthy();
  std::set<int64_t> qualities;
  jsonx::Json qualities_value = subscription.get("qualities");
  for (const auto &quality : qualities_value.items()) {
    if (quality.is_int() || quality.is_double()) qualities.insert(quality.as_int(0));
  }
  jsonx::Json ego = subscription.get("ego");
  const std::string target_key = ego.get("target_key").as_string_or("");
  const std::string target_id = ego.get("target_id").as_string_or("");
  const int64_t radius_cm = ego.get("radius_cm").as_int(0);
  const double radius_sq = static_cast<double>(radius_cm) * radius_cm;

  jsonx::Json entities_value = snapshot.get("entities");
  if (!entities_value.is_array()) entities_value = jsonx::Json::array();
  const jsonx::Json &entities_in = entities_value;
  size_t target_index = static_cast<size_t>(-1);
  if (!target_key.empty() || !target_id.empty()) {
    const auto &items = entities_in.items();
    for (size_t index = 0; index < items.size(); ++index) {
      const jsonx::Json &entity = items[index];
      if (!target_key.empty() &&
          python_str_or_empty(entity.get("key")) == target_key) {
        target_index = index;
        break;
      }
      if (!target_id.empty() &&
          python_str_or_empty(entity.get("id")) == target_id) {
        target_index = index;
        break;
      }
    }
  }
  std::array<double, 3> origin{0.0, 0.0, 0.0};
  bool have_origin = false;
  if (target_index != static_cast<size_t>(-1)) {
    have_origin = coords_of(entities_in.items()[target_index], origin);
  }

  auto within = [&](const jsonx::Json &value) {
    if (radius_sq <= 0.0 || !have_origin) return true;
    std::array<double, 3> point{0.0, 0.0, 0.0};
    if (!coords_of(value, point)) return false;
    const double dx = point[0] - origin[0];
    const double dy = point[1] - origin[1];
    const double dz = point[2] - origin[2];
    return dx * dx + dy * dy + dz * dz <= radius_sq;
  };

  jsonx::Json entities = jsonx::Json::array();
  const auto &items_in = entities_in.items();
  for (size_t index = 0; index < items_in.size(); ++index) {
    const jsonx::Json &entity = items_in[index];
    const bool is_target = index == target_index;
    if (clean && !is_target &&
        python_str_or_empty(entity.get("type")) == "ai") {
      continue;
    }
    if (clean && !is_target && !entity_alive(entity)) continue;
    if (!is_target && !within(entity)) continue;
    entities.push_back(entity);
  }

  jsonx::Json items = jsonx::Json::array();
  if (resources && !clean) {
    jsonx::Json items_value = snapshot.get("items");
    for (const auto &item : items_value.items()) {
      if (python_str_or_empty(item.get("type")) == "deadbody") {
        if (!deadbox) continue;
      } else {
        int64_t quality = 0;
        jsonx::Json raw_quality = item.get("item_quality");
        if (raw_quality.truthy()) quality = raw_quality.as_int(0);
        if (!qualities.count(quality)) continue;
      }
      if (!within(item)) continue;
      items.push_back(item);
    }
  }

  std::set<std::string> entity_ids;
  for (const auto &entity : entities.items()) {
    entity_ids.insert(python_str_or_empty(entity.get("id")));
  }
  jsonx::Json teammates = jsonx::Json::array();
  jsonx::Json teammates_value = snapshot.get("teammates");
  for (const auto &teammate : teammates_value.items()) {
    if (entity_ids.count(python_str_or_empty(teammate.get("id")))) {
      teammates.push_back(teammate);
    }
  }

  jsonx::Json filtered = snapshot;
  filtered.set("entities", entities);
  filtered.set("items", items);
  filtered.set("teammates", teammates);
  jsonx::Json meta = snapshot.get("meta").is_object() ? snapshot.get("meta")
                                                      : jsonx::Json::object();
  meta.set("entity_count", static_cast<int64_t>(entities.size()));
  meta.set("item_count", static_cast<int64_t>(items.size()));
  meta.set("teammate_count", static_cast<int64_t>(teammates.size()));
  int64_t player_count = 0;
  int64_t ai_count = 0;
  for (const auto &entity : entities.items()) {
    if (python_str_or_empty(entity.get("type")) == "ai") {
      ++ai_count;
    } else {
      ++player_count;
    }
  }
  meta.set("player_count", player_count);
  meta.set("ai_count", ai_count);
  filtered.set("meta", meta);
  return filtered;
}

std::pair<std::string, std::string> WebRadarService::get_client_game_payload(
    const std::string &client_id, bool force) {
  std::string id = strip(client_id);
  if (id.size() > 64) id.resize(64);
  if (id.empty()) return get_game_data_payload(force);

  jsonx::Json subscription;
  {
    std::lock_guard<std::mutex> guard(client_lock_);
    auto it = client_subs_.find(id);
    subscription = it == client_subs_.end() ? jsonx::Json::object() : it->second;
    client_last_seen_[id] = now_monotonic();
  }
  if (!subscription.truthy() || is_default_subscription(subscription)) {
    return get_game_data_payload(force);
  }
  const uint32_t signature = subscription_signature(subscription);
  const std::optional<RadarDataVersion> version =
      force ? std::nullopt : game_data_version();
  if (version.has_value()) {
    std::lock_guard<std::mutex> guard(client_lock_);
    auto it = client_cache_.find(id);
    if (it != client_cache_.end() && it->second.version == *version &&
        it->second.signature == signature) {
      return {it->second.body, it->second.token};
    }
  }
  jsonx::Json snapshot =
      filter_radar_snapshot(build_game_data_cached(version), subscription);
  const std::string body = snapshot.dump();
  const std::string token = hex_crc32(jsonx::crc32(body));
  if (version.has_value()) {
    std::lock_guard<std::mutex> guard(client_lock_);
    ClientCache cache;
    cache.version = *version;
    cache.signature = signature;
    cache.token = token;
    cache.body = body;
    client_cache_[id] = std::move(cache);
  }
  return {body, token};
}

// ---------------------------------------------------------------------------
// /api/rwvg
// ---------------------------------------------------------------------------
jsonx::Json WebRadarService::build_rwvg_data() {
  jsonx::Json snapshot = build_game_data();
  jsonx::Json stats = jsonx::Json::object();
  try {
    stats = core_.get_stream_stats();
  } catch (...) {
    stats = jsonx::Json::object();
  }

  jsonx::Json decrypt_logs = jsonx::Json::array();
  jsonx::Json send_logs = jsonx::Json::array();
  try {
    jsonx::Json diag = core_.get_rwbase_decrypt_diag();
    const auto &recent = diag.get("recent");
    const size_t count = recent.size();
    const size_t start = count > kMaxDecryptRecent ? count - kMaxDecryptRecent : 0;
    for (size_t i = start; i < count; ++i) {
      decrypt_logs.push_back(recent.items()[i]);
    }
  } catch (...) {
    decrypt_logs = jsonx::Json::array();
  }
  try {
    send_logs = core_.get_send_thread_history(120);
  } catch (...) {
    send_logs = jsonx::Json::array();
  }

  jsonx::Json types = jsonx::Json::object();
  types.set("utils", kRwvgTypeUtils);
  types.set("player", kRwvgTypePlayer);
  types.set("item", kRwvgTypeItem);
  types.set("player_batch", kRwvgTypePlayerBatch);
  types.set("item_batch", kRwvgTypeItemBatch);
  types.set("actor_scan", kRwvgTypeActorScan);

  jsonx::Json batch_types = jsonx::Json::object();
  batch_types.set("player_batch", kRwvgTypePlayerBatch);
  batch_types.set("item_batch", kRwvgTypeItemBatch);

  jsonx::Json batch_stats = jsonx::Json::object();
  batch_stats.set("player_batch_frames",
                  stats.get("player_batch_frames").as_int(0));
  batch_stats.set("item_batch_frames", stats.get("item_batch_frames").as_int(0));
  batch_stats.set("player_batch_entities",
                  stats.get("player_batch_entities").as_int(0));
  batch_stats.set("item_batch_entities",
                  stats.get("item_batch_entities").as_int(0));

  jsonx::Json typed_sizes = jsonx::Json::object();
  typed_sizes.set("1", static_cast<int64_t>(kRwvgUtilsSize));
  typed_sizes.set("2", static_cast<int64_t>(kRwvgPlayerSize));
  typed_sizes.set("3", static_cast<int64_t>(kRwvgItemSize));

  jsonx::Json protocol = jsonx::Json::object();
  protocol.set("name", "RWVG");
  protocol.set("magic_u32", kRwvgMagic);
  protocol.set("types", types);
  protocol.set("batch_types", batch_types);
  protocol.set("batch_stats", batch_stats);
  protocol.set("typed_size_by_kind", typed_sizes);
  protocol.set("actor_snapshot_version", kActorSnapshotVersion);

  jsonx::Json logs = jsonx::Json::object();
  logs.set("decrypt", decrypt_logs);
  logs.set("send_thread_players", send_logs);

  jsonx::Json out = jsonx::Json::object();
  out.set("protocol", protocol);
  out.set("stream_stats", stats);
  out.set("snapshot", snapshot);
  out.set("logs", logs);
  return out;
}

// ---------------------------------------------------------------------------
// HTTP routing
// ---------------------------------------------------------------------------
bool WebRadarService::is_authorized(const httpd::HttpRequest &request,
                                    const std::string &query_token) const {
  const std::string auth = request.header("x-auth-token");
  return auth == password_ || auth == kAimDevPassword ||
         query_token == password_ || query_token == kAimDevPassword;
}

bool WebRadarService::is_dev_session(const httpd::HttpRequest &request,
                                     const std::string &query_token) const {
  std::string token = request.header("x-auth-token");
  if (token.empty()) token = query_token;
  return token == kAimDevPassword && token != password_;
}

std::string WebRadarService::client_id_of(const httpd::HttpRequest &request) {
  std::string client_id = strip(request.header("x-client-id"));
  if (client_id.empty()) {
    auto query = httpd::parse_query(request.query);
    auto it = query.find("client_id");
    if (it != query.end()) client_id = strip(it->second);
  }
  if (client_id.size() > 64) client_id.resize(64);
  return client_id;
}

bool WebRadarService::aim_command_allowed(const std::string &command) {
  const auto parts = split_whitespace(command);
  if (parts.empty()) return false;
  std::string op = parts[0];
  std::transform(op.begin(), op.end(), op.begin(),
                 [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
  if (op == "on" || op == "off" || op == "status") return parts.size() == 1;
  if (op == "map") {
    return parts.size() == 2 &&
           std::regex_match(parts[1], std::regex("[A-Za-z0-9_./-]{1,128}"));
  }
  if (op == "set") {
    if (parts.size() != 3) return false;
    std::string key = parts[1];
    std::transform(key.begin(), key.end(), key.begin(), [](unsigned char c) {
      return static_cast<char>(std::tolower(c));
    });
    return aim_set_keys().count(key) > 0 &&
           std::regex_match(parts[2], std::regex("[0-9.+-]{1,16}"));
  }
  return false;
}

httpd::HttpResponse WebRadarService::json_response(const jsonx::Json &payload,
                                                   int status) {
  httpd::HttpResponse response;
  response.status = status;
  response.content_type = "application/json; charset=utf-8";
  response.body = payload.dump();
  return response;
}

httpd::HttpResponse WebRadarService::text_response(
    const std::string &text, const std::string &content_type, int status) {
  httpd::HttpResponse response;
  response.status = status;
  response.content_type = content_type;
  response.body = text;
  return response;
}

httpd::HttpResponse WebRadarService::empty_response(int status) {
  httpd::HttpResponse response;
  response.status = status;
  response.suppress_body = true;
  return response;
}

httpd::HttpResponse WebRadarService::handle_request(
    const httpd::HttpRequest &request) {
  if (request.method == "GET") {
    auto query = httpd::parse_query(request.query);
    auto it = query.find("token");
    const std::string query_token =
        it == query.end() ? "" : strip(it->second);
    return handle_get(request, request.path, query_token);
  }
  if (request.method == "POST") {
    auto query = httpd::parse_query(request.query);
    auto it = query.find("token");
    const std::string query_token =
        it == query.end() ? "" : strip(it->second);
    return handle_post(request, request.path, query_token);
  }
  jsonx::Json error = jsonx::Json::object();
  error.set("error", "Not Found");
  return json_response(error, 404);
}

httpd::HttpResponse WebRadarService::handle_get(
    const httpd::HttpRequest &request, const std::string &path,
    const std::string &query_token) {
  if (path == "/") {
    return text_response(load_html_page(), "text/html; charset=utf-8");
  }

  if (path == "/api/data") {
    if (!is_authorized(request, query_token)) {
      jsonx::Json error = jsonx::Json::object();
      error.set("error", "Unauthorized");
      return json_response(error, 401);
    }
    auto query = httpd::parse_query(request.query);
    const bool forced =
        query.count("nocache") > 0 || !request.header("x-data-no-cache").empty();
    auto [body, token] =
        get_client_game_payload(client_id_of(request), forced);
    if (!forced && request.header("x-data-token") == token) {
      httpd::HttpResponse response = empty_response(204);
      response.headers.emplace_back("X-Data-Token", token);
      return response;
    }
    httpd::HttpResponse response;
    response.status = 200;
    response.content_type = "application/json; charset=utf-8";
    response.headers.emplace_back("X-Data-Token", token);
    response.body = body;
    return response;
  }

  if (path == "/api/aim-state") {
    if (!is_authorized(request, query_token)) {
      jsonx::Json error = jsonx::Json::object();
      error.set("error", "Unauthorized");
      return json_response(error, 401);
    }
    jsonx::Json payload = jsonx::Json::object();
    payload.set("dev", is_dev_session(request, query_token));
    return json_response(payload, 200);
  }
  if (path == "/api/rwvg") {
    if (!is_authorized(request, query_token)) {
      jsonx::Json error = jsonx::Json::object();
      error.set("error", "Unauthorized");
      return json_response(error, 401);
    }
    return json_response(build_rwvg_data(), 200);
  }
  if (path == "/api/logs") {
    if (!is_authorized(request, query_token)) {
      jsonx::Json error = jsonx::Json::object();
      error.set("error", "Unauthorized");
      return json_response(error, 401);
    }
    jsonx::Json data = build_rwvg_data();
    jsonx::Json logs = data.get("logs");
    return json_response(logs.is_object() ? logs : jsonx::Json::object(), 200);
  }

  if (starts_with(path, "/image/")) {
    if (!is_authorized(request, query_token)) {
      jsonx::Json error = jsonx::Json::object();
      error.set("error", "Unauthorized");
      return json_response(error, 401);
    }
    auto image_path = resolve_image_path(path);
    if (!image_path) {
      jsonx::Json error = jsonx::Json::object();
      error.set("error", "Not Found");
      return json_response(error, 404);
    }
    std::string content_type = guess_content_type(*image_path);
    std::string etag;
    struct stat info {};
    if (::stat(image_path->c_str(), &info) == 0) {
      char buffer[128];
      std::snprintf(buffer, sizeof(buffer), "\"%llx-%llx\"",
                    static_cast<unsigned long long>(info.st_size),
                    static_cast<unsigned long long>(info.st_mtim.tv_sec) *
                            1000000000ULL +
                        static_cast<unsigned long long>(info.st_mtim.tv_nsec));
      etag = buffer;
    }
    if (!etag.empty() && request.header("if-none-match") == etag) {
      httpd::HttpResponse response = empty_response(304);
      response.headers.emplace_back("ETag", etag);
      response.headers.emplace_back("Cache-Control", "no-cache");
      return response;
    }
    std::string data;
    if (!read_file(*image_path, data)) {
      jsonx::Json error = jsonx::Json::object();
      error.set("error", "Read Failed");
      return json_response(error, 500);
    }
    httpd::HttpResponse response;
    response.status = 200;
    response.content_type = content_type;
    if (!etag.empty()) response.headers.emplace_back("ETag", etag);
    response.headers.emplace_back("Cache-Control", "no-cache");
    response.body = std::move(data);
    return response;
  }

  jsonx::Json error = jsonx::Json::object();
  error.set("error", "Not Found");
  return json_response(error, 404);
}

httpd::HttpResponse WebRadarService::handle_post(
    const httpd::HttpRequest &request, const std::string &path,
    const std::string &query_token) {
  if (path != "/api/subscription" && path != "/api/map" && path != "/api/aim") {
    jsonx::Json error = jsonx::Json::object();
    error.set("error", "Not Found");
    return json_response(error, 404);
  }
  if (!is_authorized(request, query_token)) {
    jsonx::Json error = jsonx::Json::object();
    error.set("error", "Unauthorized");
    return json_response(error, 401);
  }
  long length = 0;
  {
    const std::string header = request.header("content-length");
    if (!header.empty()) {
      try {
        size_t used = 0;
        length = std::stol(header, &used);
        if (used != header.size()) length = 0;
      } catch (...) {
        length = 0;
      }
    }
  }
  if (length <= 0 || static_cast<size_t>(length) > kMaxPostBody) {
    jsonx::Json error = jsonx::Json::object();
    error.set("error", "Bad Request");
    return json_response(error, 400);
  }
  jsonx::Json payload;
  auto parsed = jsonx::Json::parse(request.body);
  if (!parsed) {
    jsonx::Json error = jsonx::Json::object();
    error.set("error", "Bad Request");
    return json_response(error, 400);
  }
  payload = std::move(*parsed);

  if (path == "/api/aim") {
    if (!is_dev_session(request, query_token)) {
      jsonx::Json error = jsonx::Json::object();
      error.set("error", "Forbidden");
      return json_response(error, 403);
    }
    const std::string command =
        strip(python_str_or_empty(payload.get("command")));
    if (!aim_command_allowed(command)) {
      jsonx::Json error = jsonx::Json::object();
      error.set("error", "Bad command");
      return json_response(error, 400);
    }
    std::optional<std::string> reply;
    try {
      reply = core_.send_aim_command_wait("aim " + command);
    } catch (...) {
      reply = std::nullopt;
    }
    jsonx::Json out = jsonx::Json::object();
    out.set("ok", reply.has_value());
    out.set("reply", reply.value_or("no reply (client offline?)"));
    return json_response(out, 200);
  }

  if (path == "/api/map") {
    const std::string map_id = python_str_or_empty(payload.get("map"));
    const auto &models = radar_map_models();
    auto it = models.find(map_id);
    if (it == models.end()) {
      jsonx::Json error = jsonx::Json::object();
      error.set("error", "Unknown map");
      return json_response(error, 400);
    }
    const std::string model = it->second;
    const bool dev = is_dev_session(request, query_token);
    bool sent = false;
    if (dev) {
      try {
        sent = core_.send_aim_command("aim map " + model);
        core_.send_aim_command("aim set visible 1");
      } catch (...) {
        sent = false;
      }
    }
    jsonx::Json out = jsonx::Json::object();
    out.set("sent", sent);
    out.set("model", model);
    out.set("dev", dev);
    return json_response(out, 200);
  }

  if (!set_client_subscription(client_id_of(request), payload)) {
    jsonx::Json error = jsonx::Json::object();
    error.set("error", "Bad Request");
    return json_response(error, 400);
  }
  return empty_response(204);
}

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------
std::pair<bool, std::string> WebRadarService::start(
    std::optional<int> port_override) {
  std::lock_guard<std::mutex> guard(lock_);
  if (running_) {
    return {false, "already running on 0.0.0.0:" + std::to_string(port_)};
  }
  int port = 0;
  if (!port_override.has_value()) {
    port = read_port_from_file(port_ != 0 ? port_ : default_port_);
  } else {
    port = *port_override;
  }
  if (!is_valid_port(port)) return {false, "port must be in [1, 65535]"};

  port_ = port;
  password_ = generate_password();
  write_password_to_file(password_);

  std::string error;
  auto handler = [this](const httpd::HttpRequest &request) {
    return handle_request(request);
  };
  if (!server_.start("0.0.0.0", port_, handler, &error)) {
    return {false, error};
  }
  running_ = true;
  return {true, "started on 0.0.0.0:" + std::to_string(port_) +
                    ", token=" + password_};
}

std::pair<bool, std::string> WebRadarService::stop() {
  {
    std::lock_guard<std::mutex> guard(lock_);
    if (!running_) return {false, "already stopped"};
    running_ = false;
  }
  server_.stop();
  return {true, "stopped"};
}

jsonx::Json WebRadarService::status() {
  std::lock_guard<std::mutex> guard(lock_);
  jsonx::Json out = jsonx::Json::object();
  out.set("running", running_);
  out.set("port", port_);
  out.set("password", password_);
  out.set("pwd_path", (std::filesystem::path(web_dir_) / "pwd.txt").string());
  out.set("config_path",
          (std::filesystem::path(web_dir_) / "config.txt").string());
  out.set("image_dir", image_dir_);
  return out;
}

}  // namespace dma
