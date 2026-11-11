#include "http_server.h"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

#include <algorithm>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

namespace httpd {
namespace {

constexpr size_t kMaxHeaderBytes = 64 * 1024;
constexpr size_t kMaxBodyBytes = 1024 * 1024;
constexpr int kConnectionTimeoutSec = 30;

bool send_all(int fd, const char *data, size_t size) {
  size_t sent = 0;
  while (sent < size) {
    ssize_t result = ::send(fd, data + sent, size - sent, MSG_NOSIGNAL);
    if (result < 0) {
      if (errno == EINTR) continue;
      return false;
    }
    if (result == 0) return false;
    sent += static_cast<size_t>(result);
  }
  return true;
}

bool recv_exact(int fd, char *data, size_t size) {
  size_t received = 0;
  while (received < size) {
    ssize_t result = ::recv(fd, data + received, size - received, 0);
    if (result < 0) {
      if (errno == EINTR) continue;
      return false;
    }
    if (result == 0) return false;
    received += static_cast<size_t>(result);
  }
  return true;
}

const char *status_text(int status) {
  switch (status) {
    case 200: return "OK";
    case 204: return "No Content";
    case 304: return "Not Modified";
    case 400: return "Bad Request";
    case 401: return "Unauthorized";
    case 403: return "Forbidden";
    case 404: return "Not Found";
    case 405: return "Method Not Allowed";
    case 500: return "Internal Server Error";
    default: return "OK";
  }
}

std::string lower_ascii(std::string text) {
  std::transform(text.begin(), text.end(), text.begin(), [](unsigned char c) {
    return static_cast<char>(std::tolower(c));
  });
  return text;
}

bool parse_int(const std::string &text, long &value) {
  if (text.empty()) return false;
  try {
    size_t used = 0;
    long parsed = std::stol(text, &used);
    if (used != text.size()) return false;
    value = parsed;
    return true;
  } catch (...) {
    return false;
  }
}

}  // namespace

std::string strip_ascii(std::string_view text) {
  size_t begin = 0;
  size_t end = text.size();
  auto is_space = [](char c) {
    return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f' || c == '\v';
  };
  while (begin < end && is_space(text[begin])) ++begin;
  while (end > begin && is_space(text[end - 1])) --end;
  return std::string(text.substr(begin, end - begin));
}

std::string url_decode(std::string_view text) {
  std::string out;
  out.reserve(text.size());
  for (size_t i = 0; i < text.size(); ++i) {
    char c = text[i];
    if (c == '+') {
      out.push_back(' ');
    } else if (c == '%' && i + 2 < text.size()) {
      auto hex = [](char value) -> int {
        if (value >= '0' && value <= '9') return value - '0';
        if (value >= 'a' && value <= 'f') return value - 'a' + 10;
        if (value >= 'A' && value <= 'F') return value - 'A' + 10;
        return -1;
      };
      int high = hex(text[i + 1]);
      int low = hex(text[i + 2]);
      if (high >= 0 && low >= 0) {
        out.push_back(static_cast<char>((high << 4) | low));
        i += 2;
      } else {
        out.push_back(c);
      }
    } else {
      out.push_back(c);
    }
  }
  return out;
}

std::map<std::string, std::string> parse_query(std::string_view query) {
  std::map<std::string, std::string> result;
  size_t start = 0;
  while (start <= query.size()) {
    size_t amp = query.find('&', start);
    std::string_view part = amp == std::string_view::npos
                                ? query.substr(start)
                                : query.substr(start, amp - start);
    if (!part.empty()) {
      size_t eq = part.find('=');
      std::string key = url_decode(part.substr(0, eq));
      std::string value;
      if (eq != std::string_view::npos) value = url_decode(part.substr(eq + 1));
      // parse_qs(keep_blank_values=False) drops blank values entirely.
      if (!key.empty() && !value.empty()) {
        auto it = result.find(key);
        if (it == result.end()) {
          result.emplace(std::move(key), std::move(value));
        }
      }
    }
    if (amp == std::string_view::npos) break;
    start = amp + 1;
  }
  return result;
}

std::string HttpRequest::header(std::string_view name) const {
  const std::string wanted = lower_ascii(std::string(name));
  for (const auto &entry : headers) {
    if (entry.first == wanted) return entry.second;
  }
  return "";
}

HttpServer::~HttpServer() { stop(); }

bool HttpServer::start(const std::string &host, int port, Handler handler,
                       std::string *error) {
  if (running_.load()) {
    if (error) *error = "already running";
    return false;
  }
  int fd = ::socket(AF_INET, SOCK_STREAM, 0);
  if (fd < 0) {
    if (error) *error = std::strerror(errno);
    return false;
  }
  int reuse = 1;
  ::setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));

  sockaddr_in address{};
  address.sin_family = AF_INET;
  address.sin_port = htons(static_cast<uint16_t>(port));
  if (host.empty() || host == "0.0.0.0") {
    address.sin_addr.s_addr = htonl(INADDR_ANY);
  } else if (::inet_pton(AF_INET, host.c_str(), &address.sin_addr) != 1) {
    ::close(fd);
    if (error) *error = "invalid listen host";
    return false;
  }
  if (::bind(fd, reinterpret_cast<sockaddr *>(&address), sizeof(address)) != 0) {
    const std::string message = std::strerror(errno);
    ::close(fd);
    if (error) *error = message;
    return false;
  }
  if (::listen(fd, 128) != 0) {
    const std::string message = std::strerror(errno);
    ::close(fd);
    if (error) *error = message;
    return false;
  }
  timeval timeout{};
  timeout.tv_sec = kConnectionTimeoutSec;
  timeout.tv_usec = 0;
  ::setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));

  listen_fd_ = fd;
  port_ = port;
  handler_ = std::move(handler);
  running_.store(true);
  accept_thread_ = std::thread([this] { accept_loop(); });
  return true;
}

void HttpServer::stop() {
  if (!running_.exchange(false)) {
    if (accept_thread_.joinable()) accept_thread_.join();
    return;
  }
  int fd = listen_fd_;
  listen_fd_ = -1;
  if (fd >= 0) {
    ::shutdown(fd, SHUT_RDWR);
    ::close(fd);
  }
  if (accept_thread_.joinable()) accept_thread_.join();
  std::set<int> open;
  {
    std::lock_guard<std::mutex> guard(connections_lock_);
    open.swap(connections_);
  }
  // Wake blocked connection threads; they own the fd and close it themselves,
  // which avoids closing a descriptor that may already have been reused.
  for (int connection : open) {
    ::shutdown(connection, SHUT_RDWR);
  }
}

void HttpServer::accept_loop() {
  while (running_.load()) {
    sockaddr_in peer{};
    socklen_t peer_len = sizeof(peer);
    int fd = ::accept(listen_fd_, reinterpret_cast<sockaddr *>(&peer), &peer_len);
    if (fd < 0) {
      if (!running_.load()) break;
      if (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK) continue;
      if (errno == EBADF || errno == EINVAL) break;
      continue;
    }
    int one = 1;
    ::setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
    timeval timeout{};
    timeout.tv_sec = kConnectionTimeoutSec;
    timeout.tv_usec = 0;
    ::setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    ::setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    {
      std::lock_guard<std::mutex> guard(connections_lock_);
      connections_.insert(fd);
    }
    std::thread([this, fd] { handle_connection(fd); }).detach();
  }
}

void HttpServer::close_connection(int fd) {
  {
    std::lock_guard<std::mutex> guard(connections_lock_);
    connections_.erase(fd);
  }
  ::close(fd);
}

void HttpServer::handle_connection(int fd) {
  std::string buffer;
  buffer.reserve(4096);
  while (running_.load()) {
    // Read request headers up to the blank line.
    size_t header_end = std::string::npos;
    while (true) {
      header_end = buffer.find("\r\n\r\n");
      if (header_end != std::string::npos) break;
      if (buffer.size() > kMaxHeaderBytes) break;
      char chunk[4096];
      ssize_t received = ::recv(fd, chunk, sizeof(chunk), 0);
      if (received < 0) {
        if (errno == EINTR) continue;
        close_connection(fd);
        return;
      }
      if (received == 0) {
        close_connection(fd);
        return;
      }
      buffer.append(chunk, static_cast<size_t>(received));
    }
    if (header_end == std::string::npos) {
      close_connection(fd);
      return;
    }

    std::string header_block = buffer.substr(0, header_end);
    buffer.erase(0, header_end + 4);

    HttpRequest request;
    std::vector<std::string> lines;
    size_t start = 0;
    while (start <= header_block.size()) {
      size_t newline = header_block.find("\r\n", start);
      if (newline == std::string::npos) {
        lines.push_back(header_block.substr(start));
        break;
      }
      lines.push_back(header_block.substr(start, newline - start));
      start = newline + 2;
    }
    if (lines.empty()) {
      close_connection(fd);
      return;
    }
    {
      const std::string &line = lines[0];
      size_t first_space = line.find(' ');
      if (first_space == std::string::npos) {
        close_connection(fd);
        return;
      }
      size_t second_space = line.find(' ', first_space + 1);
      request.method = line.substr(0, first_space);
      request.target = second_space == std::string::npos
                           ? line.substr(first_space + 1)
                           : line.substr(first_space + 1,
                                         second_space - first_space - 1);
      request.version = second_space == std::string::npos
                            ? "HTTP/1.0"
                            : line.substr(second_space + 1);
    }
    for (size_t i = 1; i < lines.size(); ++i) {
      const std::string &line = lines[i];
      size_t colon = line.find(':');
      if (colon == std::string::npos) continue;
      std::string name = lower_ascii(strip_ascii(line.substr(0, colon)));
      std::string value = strip_ascii(line.substr(colon + 1));
      request.headers.emplace_back(std::move(name), std::move(value));
    }
    {
      size_t question = request.target.find('?');
      if (question == std::string::npos) {
        request.path = request.target;
      } else {
        request.path = request.target.substr(0, question);
        request.query = request.target.substr(question + 1);
      }
    }

    long content_length = 0;
    {
      const std::string length_header = request.header("content-length");
      if (!length_header.empty() && !parse_int(length_header, content_length)) {
        content_length = 0;
      }
    }
    if (content_length < 0 || static_cast<size_t>(content_length) > kMaxBodyBytes) {
      close_connection(fd);
      return;
    }
    if (content_length > 0) {
      const size_t needed = static_cast<size_t>(content_length);
      if (buffer.size() < needed) {
        std::string rest(needed - buffer.size(), '\0');
        if (!recv_exact(fd, rest.data(), rest.size())) {
          close_connection(fd);
          return;
        }
        buffer += rest;
      }
      request.body = buffer.substr(0, needed);
      buffer.erase(0, needed);
    }

    bool keep_alive = request.version != "HTTP/1.0";
    const std::string connection_header = lower_ascii(request.header("connection"));
    if (connection_header == "close") keep_alive = false;
    if (connection_header == "keep-alive") keep_alive = true;

    HttpResponse response;
    try {
      response = handler_ ? handler_(request) : HttpResponse{500, "", {}, "", false};
    } catch (const std::exception &error) {
      std::fprintf(stderr, "[http] handler exception: %s\n", error.what());
      response = HttpResponse{500, "application/json; charset=utf-8", {},
                              "{\"error\":\"Internal Server Error\"}", false};
    } catch (...) {
      std::fprintf(stderr, "[http] handler exception: unknown\n");
      response = HttpResponse{500, "application/json; charset=utf-8", {},
                              "{\"error\":\"Internal Server Error\"}", false};
    }

    std::string output;
    output.reserve(response.body.size() + 256);
    output += "HTTP/1.1 " + std::to_string(response.status) + " " +
              status_text(response.status) + "\r\n";
    if (!response.content_type.empty()) {
      output += "Content-Type: " + response.content_type + "\r\n";
    }
    for (const auto &header : response.headers) {
      output += header.first + ": " + header.second + "\r\n";
    }
    const bool body_allowed =
        !response.suppress_body && response.status != 204 && response.status != 304;
    if (!body_allowed) {
      output += "Content-Length: 0\r\n";
    } else {
      output += "Content-Length: " + std::to_string(response.body.size()) + "\r\n";
    }
    output += std::string("Connection: ") + (keep_alive ? "keep-alive" : "close") +
              "\r\n\r\n";
    if (body_allowed) output += response.body;

    if (!send_all(fd, output.data(), output.size())) {
      close_connection(fd);
      return;
    }
    if (!keep_alive) {
      close_connection(fd);
      return;
    }
    if (!buffer.empty() && buffer.size() > kMaxHeaderBytes) {
      close_connection(fd);
      return;
    }
  }
  close_connection(fd);
}

}  // namespace httpd
