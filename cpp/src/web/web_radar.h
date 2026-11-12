// Port of web_radar.py: authenticated HTTP API, per-client filtering and
// cached /api/data payloads.
#pragma once

#include <atomic>
#include <cstdint>
#include <map>
#include <mutex>
#include <optional>
#include <string>
#include <utility>

#include "dma_core.h"
#include "http_server.h"
#include "json.h"

namespace dma {

// Root directory holding web/, image/ and logs/ (executable dir, cwd, or
// DMA_WEBRADAR_ROOT).
std::string default_web_root_dir();

class WebRadarService {
 public:
  explicit WebRadarService(DMACore &core, int default_port = 34900,
                           const std::string &root_dir = "");

  std::pair<bool, std::string> start(std::optional<int> port_override = std::nullopt);
  std::pair<bool, std::string> stop();
  jsonx::Json status();

 private:
  struct ClientCache {
    RadarDataVersion version;
    uint32_t signature = 0;
    std::string token;
    std::string body;
  };

  static bool is_valid_port(int port);
  static std::string generate_password();
  void ensure_web_dir();
  int read_port_from_file(int fallback);
  void write_password_to_file(const std::string &password);
  std::string load_html_page();
  std::optional<std::string> resolve_image_path(const std::string &request_path);
  static std::string guess_content_type(const std::string &path);

  jsonx::Json build_game_data();
  std::optional<RadarDataVersion> game_data_version();
  jsonx::Json build_game_data_cached(const std::optional<RadarDataVersion> &version);
  std::pair<std::string, std::string> get_game_data_payload(bool force = false);
  std::pair<std::string, std::string> get_client_game_payload(
      const std::string &client_id, bool force = false);

  static jsonx::Json normalize_subscription(const jsonx::Json &payload);
  static uint32_t subscription_signature(const jsonx::Json &subscription);
  static bool is_default_subscription(const jsonx::Json &subscription);
  bool set_client_subscription(const std::string &client_id,
                               const jsonx::Json &payload);
  static jsonx::Json filter_radar_snapshot(const jsonx::Json &snapshot,
                                           const jsonx::Json &subscription);

  jsonx::Json build_rwvg_data();

  httpd::HttpResponse handle_request(const httpd::HttpRequest &request);
  httpd::HttpResponse handle_get(const httpd::HttpRequest &request,
                                 const std::string &path,
                                 const std::string &query_token);
  httpd::HttpResponse handle_post(const httpd::HttpRequest &request,
                                  const std::string &path,
                                  const std::string &query_token);

  bool is_authorized(const httpd::HttpRequest &request,
                     const std::string &query_token) const;
  bool is_dev_session(const httpd::HttpRequest &request,
                      const std::string &query_token) const;
  static std::string client_id_of(const httpd::HttpRequest &request);
  static bool aim_command_allowed(const std::string &command);

  static httpd::HttpResponse json_response(const jsonx::Json &payload,
                                           int status = 200);
  static httpd::HttpResponse text_response(const std::string &text,
                                           const std::string &content_type,
                                           int status = 200);
  static httpd::HttpResponse empty_response(int status = 204);

  DMACore &core_;
  int default_port_ = 34900;
  std::string root_dir_;
  std::string web_dir_;
  std::string image_dir_;

  std::mutex lock_;
  httpd::HttpServer server_;
  bool running_ = false;
  int port_ = 0;
  std::string password_;

  std::mutex game_data_lock_;
  std::optional<RadarDataVersion> game_data_cached_version_;
  jsonx::Json game_data_snapshot_;
  bool has_game_data_snapshot_ = false;
  std::string game_data_body_;
  std::string game_data_token_;

  std::mutex client_lock_;
  std::map<std::string, jsonx::Json> client_subs_;
  std::map<std::string, ClientCache> client_cache_;
  std::map<std::string, double> client_last_seen_;
  static constexpr size_t kMaxClientSubs = 32;
};

}  // namespace dma
