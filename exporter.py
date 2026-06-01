import os
import time
import requests
from flask import Flask, Response

JELLYFIN_URL = os.environ.get("JELLYFIN_URL", "http://jellyfin.ip:8096").rstrip("/")
JELLYFIN_API_KEY = os.environ["JELLYFIN_API_KEY"]

app = Flask(__name__)

HEADERS = {
    "X-Emby-Token": JELLYFIN_API_KEY
}


def safe_label(value):
    if value is None:
        return ""
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def metric_line(name, value, labels=None):
    labels = labels or {}
    if labels:
        label_str = ",".join([f'{k}="{safe_label(v)}"' for k, v in labels.items()])
        return f'{name}{{{label_str}}} {value}'
    return f"{name} {value}"


def get_json(path):
    r = requests.get(f"{JELLYFIN_URL}{path}", headers=HEADERS, timeout=10)
    r.raise_for_status()
    return r.json()


@app.route("/metrics")
def metrics():
    lines = []

    try:
        system = get_json("/System/Info")
        lines.append("# HELP jellyfin_up Whether Jellyfin API is reachable")
        lines.append("# TYPE jellyfin_up gauge")
        lines.append(metric_line("jellyfin_up", 1))

        lines.append("# HELP jellyfin_system_info Jellyfin system info")
        lines.append("# TYPE jellyfin_system_info gauge")
        lines.append(metric_line("jellyfin_system_info", 1, {
            "server_name": system.get("ServerName", ""),
            "version": system.get("Version", ""),
            "id": system.get("Id", "")
        }))

        lines.append(metric_line("jellyfin_pending_restart", 1 if system.get("HasPendingRestart") else 0))

    except Exception as e:
        lines.append("# HELP jellyfin_up Whether Jellyfin API is reachable")
        lines.append("# TYPE jellyfin_up gauge")
        lines.append(metric_line("jellyfin_up", 0, {"error": str(e)}))
        return Response("\n".join(lines) + "\n", mimetype="text/plain")

    try:
        sessions = get_json("/Sessions")
        active_sessions = []

        for s in sessions:
            now_playing = s.get("NowPlayingItem")
            play_state = s.get("PlayState", {})
            transcode = s.get("TranscodingInfo")

            is_playing = 1 if now_playing else 0
            if is_playing:
                active_sessions.append(s)

            labels = {
                "session_id": s.get("Id", ""),
                "username": s.get("UserName", ""),
                "client": s.get("Client", ""),
                "device": s.get("DeviceName", ""),
                "remote_endpoint": s.get("RemoteEndPoint", ""),
                "title": now_playing.get("Name", "") if now_playing else "",
                "type": now_playing.get("Type", "") if now_playing else "",
                "series": now_playing.get("SeriesName", "") if now_playing else "",
                "season": now_playing.get("ParentIndexNumber", "") if now_playing else "",
                "episode": now_playing.get("IndexNumber", "") if now_playing else "",
            }

            lines.append(metric_line("jellyfin_session_active", is_playing, labels))
            lines.append(metric_line("jellyfin_session_paused", 1 if play_state.get("IsPaused") else 0, labels))

            if transcode:
                tlabels = labels.copy()
                tlabels.update({
                    "video_codec": transcode.get("VideoCodec", ""),
                    "audio_codec": transcode.get("AudioCodec", ""),
                    "container": transcode.get("Container", ""),
                    "hardware_acceleration": str(transcode.get("HardwareAccelerationType", "")),
                    "transcode_reasons": ",".join(transcode.get("TranscodeReasons", []))
                })

                lines.append(metric_line("jellyfin_transcode_active", 1, tlabels))
                lines.append(metric_line("jellyfin_transcode_bitrate", transcode.get("Bitrate") or 0, tlabels))
                lines.append(metric_line("jellyfin_transcode_framerate", transcode.get("Framerate") or 0, tlabels))
                lines.append(metric_line("jellyfin_transcode_completion_percentage", transcode.get("CompletionPercentage") or 0, tlabels))
            else:
                lines.append(metric_line("jellyfin_transcode_active", 0, labels))

        lines.append(metric_line("jellyfin_sessions_total", len(sessions)))
        lines.append(metric_line("jellyfin_streams_active", len(active_sessions)))
        lines.append(metric_line("jellyfin_transcodes_active", sum(1 for s in sessions if s.get("TranscodingInfo"))))

    except Exception as e:
        lines.append(metric_line("jellyfin_sessions_scrape_error", 1, {"error": str(e)}))

    try:
        counts = get_json("/Items/Counts")
        for key, value in counts.items():
            if isinstance(value, int):
                lines.append(metric_line("jellyfin_media_count", value, {"type": key}))
    except Exception as e:
        lines.append(metric_line("jellyfin_counts_scrape_error", 1, {"error": str(e)}))

    try:
        users = get_json("/Users")
        lines.append(metric_line("jellyfin_users_total", len(users)))
        for u in users:
            policy = u.get("Policy", {})
            labels = {
                "username": u.get("Name", ""),
                "user_id": u.get("Id", "")
            }
            lines.append(metric_line("jellyfin_user_disabled", 1 if policy.get("IsDisabled") else 0, labels))
            lines.append(metric_line("jellyfin_user_admin", 1 if policy.get("IsAdministrator") else 0, labels))
    except Exception as e:
        lines.append(metric_line("jellyfin_users_scrape_error", 1, {"error": str(e)}))

    return Response("\n".join(lines) + "\n", mimetype="text/plain")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9594)