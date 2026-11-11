// Port of session_log.py: mirror console output into one timestamped log.
#pragma once

#include <memory>
#include <mutex>
#include <string>

class SessionLog {
 public:
  static std::unique_ptr<SessionLog> start(const std::string &root_dir,
                                           std::string *error = nullptr);
  ~SessionLog();

  SessionLog(const SessionLog &) = delete;
  SessionLog &operator=(const SessionLog &) = delete;

  const std::string &path() const { return path_; }
  void write(const std::string &text);
  void write_received_log(const std::string &message);
  void write_console_only(const std::string &text);
  void flush();

 private:
  SessionLog(std::string path, FILE *stream);

  std::string path_;
  FILE *stream_ = nullptr;
  std::mutex lock_;
};

namespace con {

// Console output helpers. When a session log is installed, print() mirrors to
// the log file exactly like the Python stdout tee; console_only() writes to
// stdout only.
void set_session_log(SessionLog *session_log);
void print(const std::string &text);
void print_line(const std::string &line);
void print_console_only(const std::string &text);

}  // namespace con
