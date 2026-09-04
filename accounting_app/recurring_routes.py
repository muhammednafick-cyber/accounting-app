from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required, current_user
import json
from datetime import datetime
from database import (
    add_recurring_template, get_due_recurring_entries, process_recurring_entry,
    get_ledgers
)
from database.recurring_db import (
    delete_recurring_template,
    get_recurring_template,
    get_recurring_templates,
    update_recurring_template,
    validate_ledger_entries,
)
from database.company_db import get_current_company_id

recurring_bp = Blueprint('recurring_bp', __name__)

VOUCHER_TYPES = ['Journal', 'Expense', 'Receipt', 'Payment', 'Contra']
FREQUENCIES = ['Monthly', 'Weekly', 'Daily', 'Yearly']


def _posted_entries():
    """The ledger rows off the form, as the template stores them.

    Blank rows are dropped rather than rejected - the form starts with one and
    the user may have added more than they filled in.
    """
    names = request.form.getlist("ledger_name[]")
    amounts = request.form.getlist("ledger_amount[]")
    types = request.form.getlist("ledger_type[]")

    entries = []
    for name, amount, side in zip(names, amounts, types):
        if not (name or "").strip() and not (amount or "").strip():
            continue
        entries.append({
            "ledger_name": (name or "").strip(),
            "amount": amount,
            "type": side,
            "cost_center_code": None,
        })
    return entries


def _clean_entries(entries, total_debit):
    """The validated rows, with amounts stored as numbers rather than strings."""
    return [{"ledger_name": e["ledger_name"],
             "amount": round(float(e["amount"]), 2),
             "type": e["type"],
             "cost_center_code": e.get("cost_center_code")}
            for e in entries]


def _form_values():
    return {
        "template_name": (request.form.get('template_name') or '').strip(),
        "voucher_type": request.form.get('voucher_type'),
        "frequency": request.form.get('frequency'),
        "next_due_date": request.form.get('next_due_date'),
        "narration": (request.form.get('narration') or '').strip(),
        "active": 1 if request.form.get('active') else 0,
    }


def _render_form(values, entries, template_id=None):
    """The form again, with what the user typed still in it.

    Sending them back to an empty form after a rejected save means retyping
    every line, which is how a balance error turns into a lost template.
    """
    return render_template('recurring/recurring_form.html',
                           ledgers=get_ledgers(),
                           values=values,
                           entries=entries,
                           template_id=template_id,
                           voucher_types=VOUCHER_TYPES,
                           frequencies=FREQUENCIES,
                           username=current_user.username)


@recurring_bp.route('/recurring/templates')
@login_required
def templates():
    return render_template('recurring/recurring_list.html',
                           templates=get_recurring_templates(),
                           username=current_user.username)


@recurring_bp.route('/recurring/add', methods=['GET', 'POST'])
@login_required
def add_template():
    if request.method == 'POST':
        values = _form_values()
        entries = _posted_entries()
        try:
            # The amount is the voucher's own total - one side of a balanced
            # entry - not a guess at which lines count for this voucher type.
            total = validate_ledger_entries(entries)
            add_recurring_template(
                values["template_name"], values["voucher_type"],
                values["frequency"], values["next_due_date"],
                json.dumps(_clean_entries(entries, total)), total,
                values["narration"])
            flash('Recurring template added.', 'success')
            return redirect(url_for('recurring_bp.templates'))
        except ValueError as exc:
            flash(str(exc), 'error')
            return _render_form(values, entries)
        except Exception as exc:
            flash(f"Could not save the template: {exc}", 'error')
            return _render_form(values, entries)

    return _render_form({"active": 1}, [])


@recurring_bp.route('/recurring/edit/<int:template_id>', methods=['GET', 'POST'])
@login_required
def edit_template(template_id):
    existing = get_recurring_template(template_id)
    if not existing:
        flash('That template no longer exists.', 'error')
        return redirect(url_for('recurring_bp.templates'))

    if request.method == 'POST':
        values = _form_values()
        entries = _posted_entries()
        try:
            total = validate_ledger_entries(entries)
            update_recurring_template(
                template_id, values["template_name"], values["voucher_type"],
                values["frequency"], values["next_due_date"],
                json.dumps(_clean_entries(entries, total)), total,
                values["narration"], values["active"])
            flash('Recurring template updated.', 'success')
            return redirect(url_for('recurring_bp.templates'))
        except ValueError as exc:
            flash(str(exc), 'error')
            return _render_form(values, entries, template_id)
        except Exception as exc:
            flash(f"Could not update the template: {exc}", 'error')
            return _render_form(values, entries, template_id)

    try:
        entries = json.loads(existing.get('ledger_details_json') or '[]')
    except (ValueError, TypeError):
        entries = []

    due = existing.get('next_due_date')
    values = {
        "template_name": existing.get('template_name'),
        "voucher_type": existing.get('voucher_type'),
        "frequency": existing.get('frequency'),
        # The column is text on some rows and a date on others; the date input
        # only accepts YYYY-MM-DD.
        "next_due_date": due.strftime('%Y-%m-%d') if hasattr(due, 'strftime') else due,
        "narration": existing.get('narration') or '',
        "active": 1 if existing.get('active') else 0,
    }
    return _render_form(values, entries, template_id)


@recurring_bp.route('/recurring/delete/<int:template_id>', methods=['POST'])
@login_required
def delete_template(template_id):
    existing = get_recurring_template(template_id)
    if not existing:
        flash('That template no longer exists.', 'error')
        return redirect(url_for('recurring_bp.templates'))
    try:
        delete_recurring_template(template_id)
        flash(f"Deleted '{existing.get('template_name')}'. "
              "Vouchers it already posted are unaffected.", 'success')
    except Exception as exc:
        flash(f"Could not delete the template: {exc}", 'error')
    return redirect(url_for('recurring_bp.templates'))


@recurring_bp.route('/recurring/process', methods=['GET', 'POST'])
@login_required
def process_entries():
    if request.method == 'POST':
        try:
            template_id = request.form.get('template_id')
            posting_date = request.form.get('posting_date') or datetime.today().strftime('%Y-%m-%d')

            voucher_no = process_recurring_entry(template_id, posting_date)

            return jsonify({'success': True, 'voucher_number': voucher_no})
        except Exception as e:
            return jsonify({'success': False, 'message': str(e)}), 400

    # GET: List due entries
    target_date = request.args.get('date') or datetime.today().strftime('%Y-%m-%d')
    due_entries = get_due_recurring_entries(target_date)

    return render_template('recurring/process_recurring.html',
                           due_entries=due_entries,
                           target_date=target_date,
                           username=current_user.username)
