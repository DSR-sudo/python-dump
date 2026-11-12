// Minimal ordered-JSON value used by the WebRadar payloads.
// The original Python service builds plain dict/list structures and calls
// json.dumps/json.loads; this is a small, dependency-free equivalent.
#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace jsonx {

class Json {
 public:
  enum class Type { Null, Bool, Int, Double, String, Array, Object };
  using Array = std::vector<Json>;
  using Object = std::vector<std::pair<std::string, Json>>;

  Json() : type_(Type::Null) {}
  Json(std::nullptr_t) : type_(Type::Null) {}
  Json(bool value) : type_(Type::Bool), bool_(value) {}
  Json(int value) : type_(Type::Int), int_(value) {}
  Json(unsigned value) : type_(Type::Int), int_(value) {}
  Json(long value) : type_(Type::Int), int_(value) {}
  Json(unsigned long value) : type_(Type::Int), int_(static_cast<int64_t>(value)) {}
  Json(long long value) : type_(Type::Int), int_(value) {}
  Json(unsigned long long value)
      : type_(Type::Int), int_(static_cast<int64_t>(value)) {}
  Json(float value) : type_(Type::Double), double_(value) {}
  Json(double value) : type_(Type::Double), double_(value) {}
  Json(const char *value) : type_(Type::String), string_(value ? value : "") {}
  Json(std::string value) : type_(Type::String), string_(std::move(value)) {}
  Json(std::string_view value) : type_(Type::String), string_(value) {}
  Json(Array value) : type_(Type::Array), array_(std::move(value)) {}
  Json(Object value) : type_(Type::Object), object_(std::move(value)) {}

  static Json array() { return Json(Array{}); }
  static Json object() { return Json(Object{}); }

  Type type() const { return type_; }
  bool is_null() const { return type_ == Type::Null; }
  bool is_bool() const { return type_ == Type::Bool; }
  bool is_number() const { return type_ == Type::Int || type_ == Type::Double; }
  bool is_int() const { return type_ == Type::Int; }
  bool is_double() const { return type_ == Type::Double; }
  bool is_string() const { return type_ == Type::String; }
  bool is_array() const { return type_ == Type::Array; }
  bool is_object() const { return type_ == Type::Object; }

  // Python truthiness for the values the service receives.
  bool truthy() const;
  bool as_bool(bool fallback = false) const;
  int64_t as_int(int64_t fallback = 0) const;
  double as_double(double fallback = 0.0) const;
  const std::string &as_string() const;
  std::string as_string_or(const std::string &fallback) const {
    return type_ == Type::String ? string_ : fallback;
  }

  const Array &items() const { return array_; }
  Array &items() { return array_; }
  const Object &members() const { return object_; }
  Object &members() { return object_; }

  size_t size() const;
  bool empty() const;

  // Object member lookup; returns nullptr when absent or not an object.
  const Json *find(std::string_view key) const;
  Json *find(std::string_view key);
  // Convenience: returns a Null Json when absent (like dict.get(key)).
  Json get(std::string_view key) const;
  // Returns fallback only when the key is absent (like dict.get(key, default)).
  Json get_or(std::string_view key, Json fallback) const;
  // Object assignment preserving first-insertion order.
  Json &set(std::string key, Json value);
  Json &operator[](std::string key) { return set(std::move(key), Json()); }
  void push_back(Json value);

  std::string dump() const;
  std::string dump_sorted() const;

  static std::optional<Json> parse(std::string_view text, std::string *error = nullptr);

  bool operator==(const Json &other) const;

 private:
  void dump_to(std::string &out, bool sort_keys) const;

  Type type_ = Type::Null;
  bool bool_ = false;
  int64_t int_ = 0;
  double double_ = 0.0;
  std::string string_;
  Array array_;
  Object object_;
};

std::string dump_double(double value);
std::string format_hex_u64(uint64_t value);
uint32_t crc32(std::string_view data);

}  // namespace jsonx
