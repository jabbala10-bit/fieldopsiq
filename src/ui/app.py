"""
Gradio UI for field operators.

Designed for use on a rugged tablet at a job site: large buttons,
microphone capture or file upload, and immediate feedback on whether
the report synced or is queued offline. Talks to the FastAPI backend
over localhost — this UI and the API are expected to run on the same
field device (ADR-009).
"""
from __future__ import annotations

import os

import gradio as gr
import httpx

API_BASE_URL = os.environ.get("FIELDOPSIQ_API_URL", "http://localhost:8000")


def submit_recording(audio_path: str | None, technician_id: str, site_id: str, language: str) -> tuple[str, str, str]:
    if not audio_path:
        return "⚠️ No audio recorded or uploaded.", "", ""
    if not technician_id or not site_id:
        return "⚠️ Technician ID and Site ID are required.", "", ""

    lang_hint = None if language == "Auto-detect" else _LANGUAGE_CODES.get(language)

    try:
        with open(audio_path, "rb") as f:
            files = {"audio_file": (os.path.basename(audio_path), f, "audio/wav")}
            data = {
                "technician_id": technician_id,
                "site_id": site_id,
                **({"language_hint": lang_hint} if lang_hint else {}),
            }
            resp = httpx.post(f"{API_BASE_URL}/jobs", files=files, data=data, timeout=120)
    except httpx.RequestError as exc:
        return f"❌ Could not reach local pipeline service: {exc}", "", ""

    if resp.status_code not in (200, 201):
        return f"❌ Pipeline error ({resp.status_code}): {resp.text[:300]}", "", ""

    result = resp.json()
    transcript_text = (result.get("transcript") or {}).get("full_text", "")
    report = result.get("report") or {}

    report_summary = _format_report(report)
    warnings = result.get("warnings") or []
    status_msg = "✅ Report created and queued for sync."
    if warnings:
        status_msg += "\n\n⚠️ " + "\n⚠️ ".join(warnings)

    return status_msg, transcript_text, report_summary


def _format_report(report: dict) -> str:
    if not report:
        return "(no report generated)"
    lines = [
        f"**Category:** {report.get('category', '—')}",
        f"**Severity:** {report.get('severity', '—')}",
        f"**Summary:** {report.get('summary', '—')}",
        f"**Equipment ID:** {report.get('equipment_id') or '—'}",
        f"**Location:** {report.get('location_detail') or '—'}",
        f"**Action Taken:** {report.get('action_taken') or '—'}",
        f"**Follow-up Required:** {'Yes' if report.get('follow_up_required') else 'No'}",
        f"**Confidence:** {report.get('extraction_confidence', 0):.0%}",
    ]
    return "\n\n".join(lines)


def check_sync_status() -> str:
    try:
        resp = httpx.get(f"{API_BASE_URL}/sync/status", timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except httpx.RequestError as exc:
        return f"❌ Could not reach pipeline service: {exc}"

    conn = data["connectivity"]
    icon = {"online": "🟢", "degraded": "🟡", "offline": "🔴"}.get(conn, "⚪")
    return (
        f"{icon} Connectivity: **{conn}**\n\n"
        f"📦 Reports queued for sync: **{data['queue_depth']}**\n\n"
        f"⚠️ Dead-letter (failed) reports: **{data['dead_letter_count']}**"
    )


def force_sync() -> str:
    try:
        resp = httpx.post(f"{API_BASE_URL}/sync/drain", timeout=60)
        resp.raise_for_status()
        results = resp.json()
    except httpx.RequestError as exc:
        return f"❌ Could not reach pipeline service: {exc}"

    if not results:
        return "ℹ️ No records synced (offline, or queue empty)."
    succeeded = sum(1 for r in results if r["success"])
    return f"✅ Synced {succeeded}/{len(results)} queued reports."


_LANGUAGE_CODES = {
    "English": "en",
    "Spanish": "es",
    "French": "fr",
    "German": "de",
    "Portuguese": "pt",
    "Mandarin": "zh",
    "Hindi": "hi",
}


def build_ui() -> gr.Blocks:
    with gr.Blocks(title="FieldOpsIQ", theme=gr.themes.Soft()) as demo:
        gr.Markdown("# 🛠️ FieldOpsIQ — Voice Field Reports")
        gr.Markdown(
            "Record or upload a voice note. It transcribes and structures "
            "**fully offline** — reports sync automatically once you're back in range."
        )

        with gr.Tab("📋 New Report"):
            with gr.Row():
                technician_id = gr.Textbox(label="Technician ID", placeholder="e.g. TECH-1042")
                site_id = gr.Textbox(label="Site ID", placeholder="e.g. SITE-WEST-07")
            language = gr.Dropdown(
                choices=["Auto-detect"] + list(_LANGUAGE_CODES.keys()),
                value="Auto-detect",
                label="Spoken Language",
            )
            audio_input = gr.Audio(sources=["microphone", "upload"], type="filepath", label="Voice Note")
            submit_btn = gr.Button("🚀 Process Report", variant="primary", size="lg")

            status_output = gr.Markdown(label="Status")
            with gr.Row():
                transcript_output = gr.Textbox(label="Transcript", lines=8, interactive=False)
                report_output = gr.Markdown(label="Structured Report")

            submit_btn.click(
                fn=submit_recording,
                inputs=[audio_input, technician_id, site_id, language],
                outputs=[status_output, transcript_output, report_output],
            )

        with gr.Tab("🔄 Sync Status"):
            gr.Markdown("Check connectivity and manually trigger a sync if you've just regained signal.")
            sync_status_output = gr.Markdown()
            with gr.Row():
                refresh_btn = gr.Button("🔍 Check Status")
                sync_btn = gr.Button("☁️ Sync Now", variant="primary")
            refresh_btn.click(fn=check_sync_status, outputs=sync_status_output)
            sync_btn.click(fn=force_sync, outputs=sync_status_output)

    return demo


if __name__ == "__main__":
    ui = build_ui()
    ui.launch(server_name="0.0.0.0", server_port=7860)
