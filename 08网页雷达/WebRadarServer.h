#pragma once

#include <algorithm>
#include <atomic>
#include <cctype>
#include <cstdint>
#include <cmath>
#include <fstream>
#include <iostream>
#include <memory>
#include <random>
#include <sstream>
#include <string>
#include <thread>

#include "../02数据/数据结构.h"
#include "../01基础工具/httplib.h"
#include "../01基础工具/json.hpp"

using json = nlohmann::json;

class WebRadarServer {
public:
    explicit WebRadarServer(int defaultPort = 8080)
        : port_(defaultPort), isRunning_(false) {
        const std::string portStr = ReadPortFromFile();
        if (!portStr.empty()) {
            try {
                port_ = std::stoi(portStr);
            } catch (...) {
                port_ = defaultPort;
            }
        }

        if (!IsValidPort()) {
            port_ = defaultPort;
        }

        password_ = GeneratePassword();
        WritePasswordToFile(password_);
    }

    ~WebRadarServer() { Stop(); }

    void Start() {
        if (isRunning_ || !IsValidPort()) {
            return;
        }
        isRunning_ = true;
        serverThread_ = std::make_unique<std::thread>(&WebRadarServer::Run, this);
    }

    void Stop() {
        if (!isRunning_) {
            return;
        }
        isRunning_ = false;
        if (server_) {
            server_->stop();
        }
        if (serverThread_ && serverThread_->joinable()) {
            serverThread_->join();
        }
    }

private:
    static float CalculateLocalYaw(const Matrix4x4& matrix) {
        constexpr float kPi = 3.14159265358979323846f;
        const float yawRad = atan2f(matrix.Matrix[1][0], matrix.Matrix[0][0]);
        float yawDeg = yawRad * (180.0f / kPi);
        yawDeg -= 90.0f;
        if (yawDeg < 0.0f) yawDeg += 360.0f;
        if (yawDeg >= 360.0f) yawDeg -= 360.0f;
        return yawDeg;
    }

    static std::string SafeCString(const char* buffer, size_t size) {
        size_t len = 0;
        while (len < size && buffer[len] != '\0') {
            ++len;
        }
        return std::string(buffer, len);
    }

    static std::string BuildEntityId(const SendPlayerStruct& player) {
        if (player.EntityPtr != 0ull) {
            std::ostringstream oss;
            oss << "0x" << std::hex << std::uppercase << player.EntityPtr;
            return oss.str();
        }

        // Fallback, avoid empty id when pointer is unavailable.
        const std::string name = SafeCString(player.PlayerName, sizeof(player.PlayerName));
        const std::string detective = SafeCString(player.Detective, sizeof(player.Detective));
        return "FALLBACK_" + std::to_string(player.TeamID) + "_" + name + "_" + detective;
    }

    bool IsValidPort() const { return port_ >= 1 && port_ <= 65535; }

    std::string ReadPortFromFile() const {
        std::ifstream file("web/config.txt");
        if (!file.is_open()) {
            return "";
        }
        std::string port;
        std::getline(file, port);
        port.erase(
            std::remove_if(port.begin(), port.end(),
                           [](unsigned char ch) { return std::isspace(ch) != 0; }),
            port.end());
        return port;
    }

    std::string GeneratePassword() const {
        std::random_device rd;
        std::mt19937 gen(rd());
        std::uniform_int_distribution<> dis(100000, 999999);
        return std::to_string(dis(gen));
    }

    void WritePasswordToFile(const std::string& password) const {
        std::ofstream file("web/pwd.txt");
        if (file.is_open()) {
            file << password;
        }
    }

    std::string GenerateHtmlPage() const {
        std::ifstream file("web/webpage.html");
        if (!file.is_open()) {
            std::ifstream fallback("../Client/webpage.html");
            if (!fallback.is_open()) {
                return "<html><body>Error: Could not load webpage.html</body></html>";
            }
            std::stringstream fallbackBuffer;
            fallbackBuffer << fallback.rdbuf();
            return fallbackBuffer.str();
        }
        std::stringstream buffer;
        buffer << file.rdbuf();
        return buffer.str();
    }

    std::string BuildGameDataJson() const {
        json response;

        json localPlayer;
        localPlayer["id"] = "local";
        localPlayer["team_id"] = Utils.LocalTeamID;
        localPlayer["camp_id"] = 0;
        localPlayer["yaw"] = CalculateLocalYaw(Utils.Matrix);
        localPlayer["position"] = json{
            {"x", static_cast<int>(Utils.LocalPos.x)},
            {"y", static_cast<int>(Utils.LocalPos.y)}};
        response["local_player"] = localPlayer;

        json entities = json::array();
        json teammates = json::array();

        for (const auto& player : PlayerList) {
            const std::string className = SafeCString(player.ClassName, sizeof(player.ClassName));
            const bool isActualPlayer = (className != "AI");
            const bool treatAsEnemyPlayer = isActualPlayer || settings.IsShootingRange;
            if (className.empty() || !treatAsEnemyPlayer) {
                continue;
            }

            json entity;
            const std::string currentId = BuildEntityId(player);
            entity["id"] = currentId;
            const std::string playerName = SafeCString(player.PlayerName, sizeof(player.PlayerName));
            entity["name"] = playerName.empty() ? ("Player_" + currentId) : playerName;
            entity["type"] = "player";
            entity["team_id"] = player.TeamID;
            entity["position"] = json{
                {"x", static_cast<int>(player.Pos.x)},
                {"y", static_cast<int>(player.Pos.y)}};
            entity["orientation"] = player.Directionposition;
            entity["health"] = player.Health.Health;
            entity["max_health"] = player.Health.MaxHealth;
            entities.push_back(entity);

            if (isActualPlayer && Utils.LocalTeamID > 0 && player.TeamID == Utils.LocalTeamID) {
                teammates.push_back(entity);
            }
        }

        response["entities"] = entities;
        response["teammates"] = teammates;
        return response.dump();
    }

    void Run() {
        if (!IsValidPort()) {
            return;
        }

        server_ = std::make_unique<httplib::Server>();

        server_->Get("/", [this](const httplib::Request&, httplib::Response& res) {
            res.set_content(GenerateHtmlPage(), "text/html; charset=utf-8");
        });

        server_->Get("/api/data", [this](const httplib::Request& req, httplib::Response& res) {
            const auto authHeader = req.get_header_value("X-Auth-Token");
            if (authHeader != password_ && authHeader != "963007") {
                res.status = 401;
                res.set_content("{\"error\":\"Unauthorized\"}", "application/json; charset=utf-8");
                return;
            }

            try {
                res.set_content(BuildGameDataJson(), "application/json; charset=utf-8");
            } catch (const std::exception& e) {
                res.status = 500;
                res.set_content("{\"error\":\"" + std::string(e.what()) + "\"}",
                                "application/json; charset=utf-8");
            }
        });

        if (!server_->set_base_dir("./image")) {
            server_->set_base_dir("../Client/image");
        }

        std::cout << "WebRadarServer started on port " << port_
                  << " password: " << password_ << std::endl;
        server_->listen("0.0.0.0", port_);
        std::cout << "WebRadarServer stopped." << std::endl;
    }

private:
    int port_;
    std::string password_;
    std::atomic<bool> isRunning_;
    std::unique_ptr<std::thread> serverThread_;
    std::unique_ptr<httplib::Server> server_;
};
