#include "json/json.h"

#include <algorithm>
#include <charconv>
#include <cmath>
#include <cstdio>
#include <cstring>

namespace jsonx {
namespace {

constexpr int kMaxDepth = 128;

void append_utf8(std::string &out, uint32_t cp) {
  if (cp <= 0x7F) {
    out.push_back(static_cast<char>(cp));
  } else if (cp <= 0x7FF) {
    out.push_back(static_cast<char>(0xC0 | (cp >> 6)));
    out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
  } else if (cp <= 0xFFFF) {
    out.push_back(static_cast<char>(0xE0 | (cp >> 12)));
    out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
    out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
  } else {
    out.push_back(static_cast<char>(0xF0 | (cp >> 18)));
    out.push_back(static_cast<char>(0x80 | ((cp >> 12) & 0x3F)));
    out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
    out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
  }
}

void append_escaped(std::string &out, std::string_view s) {
  for (unsigned char c : s) {
    switch (c) {
      case '"': out += "\\\""; break;
      case '\\': out += "\\\\"; break;
      case '\b': out += "\\b"; break;
      case '\f': out += "\\f"; break;
      case '\n': out += "\\n"; break;
      case '\r': out += "\\r"; break;
      case '\t': out += "\\t"; break;
      default:
        if (c < 0x20) {
          char buf[8];
          std::snprintf(buf, sizeof(buf), "\\u%04x", c);
          out += buf;
        } else {
          out.push_back(static_cast<char>(c));
        }
        break;
    }
  }
}

class Parser {
 public:
  explicit Parser(std::string_view text) : text_(text) {}

  bool parse(Json &out, std::string *error) {
    skip_ws();
    if (!parse_value(out, 0)) {
      if (error) *error = error_;
      return false;
    }
    skip_ws();
    if (pos_ != text_.size()) {
      if (error) *error = "trailing characters";
      return false;
    }
    return true;
  }

 private:
  void skip_ws() {
    while (pos_ < text_.size()) {
      char c = text_[pos_];
      if (c == ' ' || c == '\t' || c == '\n' || c == '\r') {
        ++pos_;
      } else {
        break;
      }
    }
  }

  bool fail(const char *message) {
    if (error_.empty()) error_ = message;
    return false;
  }

  bool parse_value(Json &out, int depth) {
    if (depth > kMaxDepth) return fail("nesting too deep");
    skip_ws();
    if (pos_ >= text_.size()) return fail("unexpected end of input");
    switch (text_[pos_]) {
      case 'n':
        if (text_.substr(pos_, 4) == "null") {
          pos_ += 4;
          out = Json();
          return true;
        }
        return fail("invalid literal");
      case 't':
        if (text_.substr(pos_, 4) == "true") {
          pos_ += 4;
          out = Json(true);
          return true;
        }
        return fail("invalid literal");
      case 'f':
        if (text_.substr(pos_, 5) == "false") {
          pos_ += 5;
          out = Json(false);
          return true;
        }
        return fail("invalid literal");
      case '"': {
        std::string value;
        if (!parse_string(value)) return false;
        out = Json(std::move(value));
        return true;
      }
      case '[': return parse_array(out, depth);
      case '{': return parse_object(out, depth);
      default: return parse_number(out);
    }
  }

  bool parse_array(Json &out, int depth) {
    ++pos_;  // '['
    Json::Array values;
    skip_ws();
    if (pos_ < text_.size() && text_[pos_] == ']') {
      ++pos_;
      out = Json(std::move(values));
      return true;
    }
    while (true) {
      Json value;
      if (!parse_value(value, depth + 1)) return false;
      values.push_back(std::move(value));
      skip_ws();
      if (pos_ >= text_.size()) return fail("unterminated array");
      if (text_[pos_] == ',') {
        ++pos_;
        continue;
      }
      if (text_[pos_] == ']') {
        ++pos_;
        break;
      }
      return fail("invalid array separator");
    }
    out = Json(std::move(values));
    return true;
  }

  bool parse_object(Json &out, int depth) {
    ++pos_;  // '{'
    Json::Object members;
    skip_ws();
    if (pos_ < text_.size() && text_[pos_] == '}') {
      ++pos_;
      out = Json(std::move(members));
      return true;
    }
    while (true) {
      skip_ws();
      if (pos_ >= text_.size() || text_[pos_] != '"') return fail("expected object key");
      std::string key;
      if (!parse_string(key)) return false;
      skip_ws();
      if (pos_ >= text_.size() || text_[pos_] != ':') return fail("expected ':'");
      ++pos_;
      Json value;
      if (!parse_value(value, depth + 1)) return false;
      bool replaced = false;
      for (auto &member : members) {
        if (member.first == key) {
          member.second = std::move(value);
          replaced = true;
          break;
        }
      }
      if (!replaced) members.emplace_back(std::move(key), std::move(value));
      skip_ws();
      if (pos_ >= text_.size()) return fail("unterminated object");
      if (text_[pos_] == ',') {
        ++pos_;
        continue;
      }
      if (text_[pos_] == '}') {
        ++pos_;
        break;
      }
      return fail("invalid object separator");
    }
    out = Json(std::move(members));
    return true;
  }

  bool parse_hex4(uint32_t &value) {
    if (pos_ + 4 > text_.size()) return fail("short unicode escape");
    value = 0;
    for (int i = 0; i < 4; ++i) {
      char c = text_[pos_++];
      value <<= 4;
      if (c >= '0' && c <= '9') {
        value |= static_cast<uint32_t>(c - '0');
      } else if (c >= 'a' && c <= 'f') {
        value |= static_cast<uint32_t>(c - 'a' + 10);
      } else if (c >= 'A' && c <= 'F') {
        value |= static_cast<uint32_t>(c - 'A' + 10);
      } else {
        return fail("invalid unicode escape");
      }
    }
    return true;
  }

  bool parse_string(std::string &out) {
    ++pos_;  // opening quote
    out.clear();
    while (pos_ < text_.size()) {
      unsigned char c = static_cast<unsigned char>(text_[pos_++]);
      if (c == '"') return true;
      if (c == '\\') {
        if (pos_ >= text_.size()) return fail("unterminated escape");
        char esc = text_[pos_++];
        switch (esc) {
          case '"': out.push_back('"'); break;
          case '\\': out.push_back('\\'); break;
          case '/': out.push_back('/'); break;
          case 'b': out.push_back('\b'); break;
          case 'f': out.push_back('\f'); break;
          case 'n': out.push_back('\n'); break;
          case 'r': out.push_back('\r'); break;
          case 't': out.push_back('\t'); break;
          case 'u': {
            uint32_t cp = 0;
            if (!parse_hex4(cp)) return false;
            if (cp >= 0xD800 && cp <= 0xDBFF && pos_ + 1 < text_.size() &&
                text_[pos_] == '\\' && text_[pos_ + 1] == 'u') {
              pos_ += 2;
              uint32_t low = 0;
              if (!parse_hex4(low)) return false;
              if (low >= 0xDC00 && low <= 0xDFFF) {
                cp = 0x10000 + ((cp - 0xD800) << 10) + (low - 0xDC00);
              } else {
                append_utf8(out, cp);
                cp = low;
              }
            }
            append_utf8(out, cp);
            break;
          }
          default: return fail("invalid escape");
        }
      } else if (c < 0x20) {
        return fail("unescaped control character");
      } else {
        out.push_back(static_cast<char>(c));
      }
    }
    return fail("unterminated string");
  }

  bool parse_number(Json &out) {
    size_t start = pos_;
    if (pos_ < text_.size() && text_[pos_] == '-') ++pos_;
    if (pos_ >= text_.size()) return fail("invalid number");
    if (text_[pos_] == '0') {
      ++pos_;
    } else if (text_[pos_] >= '1' && text_[pos_] <= '9') {
      while (pos_ < text_.size() && text_[pos_] >= '0' && text_[pos_] <= '9') ++pos_;
    } else {
      return fail("invalid number");
    }
    bool is_double = false;
    if (pos_ < text_.size() && text_[pos_] == '.') {
      is_double = true;
      ++pos_;
      size_t digits = 0;
      while (pos_ < text_.size() && text_[pos_] >= '0' && text_[pos_] <= '9') {
        ++pos_;
        ++digits;
      }
      if (digits == 0) return fail("invalid number");
    }
    if (pos_ < text_.size() && (text_[pos_] == 'e' || text_[pos_] == 'E')) {
      is_double = true;
      ++pos_;
      if (pos_ < text_.size() && (text_[pos_] == '+' || text_[pos_] == '-')) ++pos_;
      size_t digits = 0;
      while (pos_ < text_.size() && text_[pos_] >= '0' && text_[pos_] <= '9') {
        ++pos_;
        ++digits;
      }
      if (digits == 0) return fail("invalid number");
    }
    std::string_view token = text_.substr(start, pos_ - start);
    if (!is_double) {
      int64_t value = 0;
      auto result = std::from_chars(token.data(), token.data() + token.size(), value);
      if (result.ec == std::errc() && result.ptr == token.data() + token.size()) {
        out = Json(value);
        return true;
      }
    }
    double value = 0.0;
    auto result = std::from_chars(token.data(), token.data() + token.size(), value);
    if (result.ec != std::errc()) return fail("invalid number");
    out = Json(value);
    return true;
  }

  std::string_view text_;
  size_t pos_ = 0;
  std::string error_;
};

}  // namespace

bool Json::truthy() const {
  switch (type_) {
    case Type::Null: return false;
    case Type::Bool: return bool_;
    case Type::Int: return int_ != 0;
    case Type::Double: return double_ != 0.0;
    case Type::String: return !string_.empty();
    case Type::Array: return !array_.empty();
    case Type::Object: return !object_.empty();
  }
  return false;
}

bool Json::as_bool(bool fallback) const {
  switch (type_) {
    case Type::Bool: return bool_;
    case Type::Null: return false;
    case Type::Int: return int_ != 0;
    case Type::Double: return double_ != 0.0;
    case Type::String: return !string_.empty();
    case Type::Array: return !array_.empty();
    case Type::Object: return !object_.empty();
  }
  return fallback;
}

int64_t Json::as_int(int64_t fallback) const {
  switch (type_) {
    case Type::Bool: return bool_ ? 1 : 0;
    case Type::Int: return int_;
    case Type::Double:
      if (!std::isfinite(double_)) return fallback;
      return static_cast<int64_t>(double_);
    case Type::String: {
      if (string_.empty()) return fallback;
      try {
        size_t used = 0;
        long long value = std::stoll(string_, &used, 10);
        if (used != string_.size()) return fallback;
        return value;
      } catch (...) {
        return fallback;
      }
    }
    default: return fallback;
  }
}

double Json::as_double(double fallback) const {
  switch (type_) {
    case Type::Bool: return bool_ ? 1.0 : 0.0;
    case Type::Int: return static_cast<double>(int_);
    case Type::Double: return double_;
    case Type::String: {
      if (string_.empty()) return fallback;
      try {
        size_t used = 0;
        double value = std::stod(string_, &used);
        if (used != string_.size()) return fallback;
        return value;
      } catch (...) {
        return fallback;
      }
    }
    default: return fallback;
  }
}

const std::string &Json::as_string() const {
  static const std::string kEmpty;
  return type_ == Type::String ? string_ : kEmpty;
}

size_t Json::size() const {
  switch (type_) {
    case Type::Array: return array_.size();
    case Type::Object: return object_.size();
    case Type::String: return string_.size();
    default: return 0;
  }
}

bool Json::empty() const {
  switch (type_) {
    case Type::Array: return array_.empty();
    case Type::Object: return object_.empty();
    case Type::String: return string_.empty();
    default: return type_ == Type::Null;
  }
}

const Json *Json::find(std::string_view key) const {
  if (type_ != Type::Object) return nullptr;
  for (const auto &member : object_) {
    if (member.first == key) return &member.second;
  }
  return nullptr;
}

Json *Json::find(std::string_view key) {
  if (type_ != Type::Object) return nullptr;
  for (auto &member : object_) {
    if (member.first == key) return &member.second;
  }
  return nullptr;
}

Json Json::get(std::string_view key) const {
  const Json *value = find(key);
  return value ? *value : Json();
}

Json Json::get_or(std::string_view key, Json fallback) const {
  const Json *value = find(key);
  return value ? *value : std::move(fallback);
}

Json &Json::set(std::string key, Json value) {
  if (type_ != Type::Object) {
    type_ = Type::Object;
    object_.clear();
  }
  for (auto &member : object_) {
    if (member.first == key) {
      member.second = std::move(value);
      return member.second;
    }
  }
  object_.emplace_back(std::move(key), std::move(value));
  return object_.back().second;
}

void Json::push_back(Json value) {
  if (type_ != Type::Array) {
    type_ = Type::Array;
    array_.clear();
  }
  array_.push_back(std::move(value));
}

bool Json::operator==(const Json &other) const {
  if (type_ != other.type_) return false;
  switch (type_) {
    case Type::Null: return true;
    case Type::Bool: return bool_ == other.bool_;
    case Type::Int: return int_ == other.int_;
    case Type::Double: return double_ == other.double_;
    case Type::String: return string_ == other.string_;
    case Type::Array: return array_ == other.array_;
    case Type::Object:
      if (object_.size() != other.object_.size()) return false;
      for (const auto &member : object_) {
        const Json *value = other.find(member.first);
        if (!value || !(*value == member.second)) return false;
      }
      return true;
  }
  return false;
}

std::string dump_double(double value) {
  if (std::isnan(value)) return "NaN";
  if (std::isinf(value)) return value < 0 ? "-Infinity" : "Infinity";
  char buf[64];
  auto result = std::to_chars(buf, buf + sizeof(buf), value);
  std::string text(buf, result.ptr);
  // Match Python's repr(): integral doubles keep a trailing ".0" until the
  // point where repr switches to exponent notation.
  if (text.find('.') == std::string::npos && text.find('e') == std::string::npos &&
      text.find('E') == std::string::npos && text.size() < 16) {
    text += ".0";
  }
  return text;
}

std::string format_hex_u64(uint64_t value) {
  char buf[32];
  std::snprintf(buf, sizeof(buf), "%llX", static_cast<unsigned long long>(value));
  return std::string(buf);
}

uint32_t crc32(std::string_view data) {
  uint32_t crc = 0xFFFFFFFFu;
  for (unsigned char c : data) {
    crc ^= c;
    for (int i = 0; i < 8; ++i) {
      crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
    }
  }
  return ~crc;
}

void Json::dump_to(std::string &out, bool sort_keys) const {
  switch (type_) {
    case Type::Null: out += "null"; break;
    case Type::Bool: out += bool_ ? "true" : "false"; break;
    case Type::Int: out += std::to_string(int_); break;
    case Type::Double: out += dump_double(double_); break;
    case Type::String:
      out.push_back('"');
      append_escaped(out, string_);
      out.push_back('"');
      break;
    case Type::Array: {
      out.push_back('[');
      bool first = true;
      for (const auto &value : array_) {
        if (!first) out.push_back(',');
        first = false;
        value.dump_to(out, sort_keys);
      }
      out.push_back(']');
      break;
    }
    case Type::Object: {
      const Object *members = &object_;
      Object sorted;
      if (sort_keys) {
        sorted = object_;
        std::sort(sorted.begin(), sorted.end(),
                  [](const auto &a, const auto &b) { return a.first < b.first; });
        members = &sorted;
      }
      out.push_back('{');
      bool first = true;
      for (const auto &member : *members) {
        if (!first) out.push_back(',');
        first = false;
        out.push_back('"');
        append_escaped(out, member.first);
        out += "\":";
        member.second.dump_to(out, sort_keys);
      }
      out.push_back('}');
      break;
    }
  }
}

std::string Json::dump() const {
  std::string out;
  dump_to(out, false);
  return out;
}

std::string Json::dump_sorted() const {
  std::string out;
  dump_to(out, true);
  return out;
}

std::optional<Json> Json::parse(std::string_view text, std::string *error) {
  Parser parser(text);
  Json value;
  if (!parser.parse(value, error)) return std::nullopt;
  return value;
}

}  // namespace jsonx
