// Small HTTP/1.1 server (keep-alive, thread per connection) replacing
// Python's ThreadingHTTPServer + BaseHTTPRequestHandler usage.
#pragma once

#include <atomic>
#include <cstdint>
#include <functional>
#include <map>
#include <mutex>
#include <set>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

namespace httpd {

struct HttpRequest {
  std::string method;
  std::string target;
  std::string path;
  std::string query;
  std::string version;
  std::vector<std::pair<std::string, std::string>> headers;  // lower-case names
  std::string body;

  std::string header(std::string_view name) const;
  bool has_header(std::string_view name) const { return !header(name).empty(); }
};

struct HttpResponse {
  int status = 200;
  std::string content_type;
  std::vector<std::pair<std::string, std::string>> headers;
  std::string body;
  bool suppress_body = false;
};

class HttpServer {
 public:
  using Handler = std::function<HttpResponse(const HttpRequest &)>;

  HttpServer() = default;
  ~HttpServer();
  HttpServer(const HttpServer &) = delete;
  HttpServer &operator=(const HttpServer &) = delete;

  bool start(const std::string &host, int port, Handler handler,
             std::string *error);
  void stop();
  bool running() const { return running_.load(); }
  int port() const { return port_; }

 private:
  void accept_loop();
  void handle_connection(int fd);
  void close_connection(int fd);

  std::atomic<bool> running_{false};
  int listen_fd_ = -1;
  int port_ = 0;
  Handler handler_;
  std::thread accept_thread_;
  std::mutex connections_lock_;
  std::set<int> connections_;
};

// URL/query helpers mirroring urllib.parse.parse_qs (keep_blank_values=False).
std::string url_decode(std::string_view text);
std::string strip_ascii(std::string_view text);
std::map<std::string, std::string> parse_query(std::string_view query);

}  // namespace httpd
