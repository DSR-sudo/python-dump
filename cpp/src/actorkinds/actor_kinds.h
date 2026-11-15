// Port of python/web_actor_kinds.py: Actor kind monitoring HTTP service
// (default 34901), sharing web/pwd.txt with WebRadar.
#pragma once

#include <mutex>
#include <optional>
#include <string>
#include <utility>

#include "http/http_server.h"
#include "json/json.h"

namespace dma {

class DMACore;

class ActorkindsService {
 public:
  ActorkindsService(DMACore &core, int default_port,
                    const std::string &root_dir);

  std::pair<bool, std::string> start(
      std::optional<int> port_override = std::nullopt);
  std::pair<bool, std::string> stop();

  bool running() const { return server_.running(); }
  int port() const { return port_; }
  const std::string &password() const { return password_; }
  std::string pwd_path() const;
  jsonx::Json status() const;

 private:
  httpd::HttpResponse handle(const httpd::HttpRequest &request) const;
  bool is_authorized(const httpd::HttpRequest &request,
                     const std::string &query_token) const;
  jsonx::Json build_actor_kinds_data() const;
  std::string load_html_page() const;
  int read_port_from_file(int fallback) const;
  std::string load_shared_password() const;
  void write_password_to_file(const std::string &password) const;

  DMACore &core_;
  int default_port_;
  std::string root_dir_;
  std::string web_dir_;
  int port_ = 0;
  std::string password_;
  httpd::HttpServer server_;
  mutable std::mutex start_lock_;
};

}  // namespace dma
