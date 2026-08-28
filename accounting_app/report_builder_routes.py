"""HTTP endpoints for the Custom Report Builder.

The browser never sends SQL - only a definition (dataset key, column refs,
filters). report_builder compiles it, and every table in the result is scoped
to the signed-in user's company.
"""
import io

import pandas as pd
from flask import Blueprint, render_template, request, jsonify, send_file
from flask_login import login_required, current_user

from database.company_db import get_current_company_id
from database.report_builder_db import (
    list_reports, get_report, save_report, delete_report,
    init_report_builder_tables,
)
from . import report_builder as RB

report_builder_bp = Blueprint("report_builder_bp", __name__)

PERMISSION = "reports.report_builder"


def _denied():
    return jsonify({"success": False,
                    "message": "You do not have access to the report builder."}), 403


def _allowed():
    try:
        return current_user.is_authenticated and current_user.can_access(PERMISSION)
    except Exception:
        return False


@report_builder_bp.route("/report/builder")
@login_required
def report_builder_page():
    init_report_builder_tables()
    return render_template("report_builder.html",
                           schema=RB.describe_schema(),
                           saved=list_reports(),
                           max_rows=RB.MAX_ROWS,
                           default_rows=RB.DEFAULT_ROWS)


@report_builder_bp.route("/api/report_builder/schema")
@login_required
def schema():
    if not _allowed():
        return _denied()
    return jsonify({"success": True, "data": RB.describe_schema()})


def _definition_from_request():
    payload = request.get_json(silent=True) or {}
    return payload.get("definition") or payload


@report_builder_bp.route("/api/report_builder/run", methods=["POST"])
@login_required
def run():
    if not _allowed():
        return _denied()
    company_id = get_current_company_id()
    definition = _definition_from_request()

    try:
        result = RB.run_report(definition, company_id)
    except RB.ReportError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        print(f"[report-builder] run failed: {exc}")
        return jsonify({"success": False,
                        "message": f"The report could not be run: {exc}"}), 500

    # Park the rows so the export buttons - and the chat's own exporter - can
    # reach them without recomputing.
    token = None
    try:
        from .chat_export_store import remember
        title = (definition.get("name") or "Custom Report").strip()[:60]
        token = remember(result["columns"], result["rows"], title=title or "Custom Report")
    except Exception as exc:
        print(f"[report-builder] could not cache result: {exc}")

    result["export_token"] = token
    return jsonify({"success": True, "data": result})


@report_builder_bp.route("/api/report_builder/sql", methods=["POST"])
@login_required
def preview_sql():
    """Show the SQL the builder would run - useful for checking a definition."""
    if not _allowed():
        return _denied()
    try:
        sql, params, _ = RB.compile_report(_definition_from_request(),
                                           get_current_company_id())
    except RB.ReportError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    return jsonify({"success": True, "data": {"sql": sql, "parameters": params}})


@report_builder_bp.route("/api/report_builder/export", methods=["POST"])
@login_required
def export():
    """Run the report and download it as .xlsx, .csv or .pdf."""
    if not _allowed():
        return _denied()

    payload = request.get_json(silent=True) or {}
    definition = payload.get("definition") or payload
    fmt = (payload.get("format") or "xlsx").strip().lower()
    name = (definition.get("name") or "Custom Report").strip() or "Custom Report"
    safe_name = "".join(c for c in name if c not in '\\/:*?"<>|')[:60]

    try:
        result = RB.run_report(definition, get_current_company_id())
    except RB.ReportError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    df = pd.DataFrame(result["rows"], columns=result["columns"])

    if fmt == "csv":
        buffer = io.BytesIO(df.to_csv(index=False).encode("utf-8-sig"))
        return send_file(buffer, download_name=safe_name + ".csv",
                         as_attachment=True, mimetype="text/csv")

    if fmt == "pdf":
        from .export_routes import _chat_result_pdf
        pdf = _chat_result_pdf({"title": name, "columns": result["columns"],
                                "rows": result["rows"]})
        if pdf is None:
            return jsonify({"success": False,
                            "message": "PDF export needs the reportlab package. "
                                       "Use Excel or CSV instead."}), 501
        return send_file(pdf, download_name=safe_name + ".pdf",
                         as_attachment=True, mimetype="application/pdf")

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Report")
        sheet = writer.sheets["Report"]
        for index, column in enumerate(df.columns, start=1):
            widest = max([len(str(column))] +
                         [len(str(v)) for v in df[column].head(200)] or [0])
            sheet.column_dimensions[
                sheet.cell(row=1, column=index).column_letter
            ].width = min(max(widest + 2, 10), 55)
    output.seek(0)
    return send_file(
        output, download_name=safe_name + ".xlsx", as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@report_builder_bp.route("/api/report_builder/reports")
@login_required
def saved_reports():
    if not _allowed():
        return _denied()
    return jsonify({"success": True, "data": list_reports()})


@report_builder_bp.route("/api/report_builder/reports/<int:report_id>")
@login_required
def load_saved(report_id):
    if not _allowed():
        return _denied()
    report = get_report(report_id)
    if not report:
        return jsonify({"success": False, "message": "Report not found."}), 404
    return jsonify({"success": True, "data": report})


@report_builder_bp.route("/api/report_builder/reports", methods=["POST"])
@login_required
def save():
    if not _allowed():
        return _denied()
    payload = request.get_json(silent=True) or {}
    definition = payload.get("definition") or {}

    # Refuse to save something that cannot run - a saved report that errors on
    # open is worse than a rejected save.
    try:
        RB.compile_report(definition, get_current_company_id())
    except RB.ReportError as exc:
        return jsonify({"success": False,
                        "message": f"That report is not valid yet: {exc}"}), 400

    try:
        report_id = save_report(
            payload.get("name"), payload.get("description"), definition,
            created_by=getattr(current_user, "username", None),
            report_id=payload.get("id"))
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        print(f"[report-builder] save failed: {exc}")
        return jsonify({"success": False, "message": str(exc)}), 500

    return jsonify({"success": True, "data": {"id": report_id,
                                              "saved": list_reports()}})


@report_builder_bp.route("/api/report_builder/reports/<int:report_id>",
                         methods=["DELETE"])
@login_required
def remove(report_id):
    if not _allowed():
        return _denied()
    if not delete_report(report_id):
        return jsonify({"success": False, "message": "Report not found."}), 404
    return jsonify({"success": True, "data": {"saved": list_reports()}})
