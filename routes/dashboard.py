"""Browse saved transactions, view balances and edit local classifications."""

import re
import hmac
from datetime import datetime, timezone

from flask import Blueprint, jsonify, render_template, request, redirect, url_for, current_app, session
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import load_only, selectinload
from werkzeug.exceptions import BadRequest, NotFound, Conflict

from models import Account, Transaction, TransactionIncome, IncomeStream, IncomeShift
from services.transactions.shifts import attach_shift_totals
from uuid import uuid4
from services.transactions.income_streams import STREAM_KINDS, FORECAST_PERIODS, FORECAST_CURRENCIES, TAX_YEARS, active_days, annual_gross, holiday_amount, stream_id, stream_fields
from routes.helpers import query_values
from services.database.connection import db
from services.web.activity import record_action
from services.error_logging import log_failure
from services.transactions.income import TAX_TREATMENTS, save_income
from services.transactions.review import REVIEW_FIELDS, review_version
from services.transactions.income_form import FIELDS as INCOME_FORM_FIELDS, display_amount, decimal_places, form_values
from services.transactions.automatic_sync import latest_sync, start_dashboard_sync
from services.transactions.classification import CLASSIFICATION_TYPES, classification_details, save_classification, clear_classification
from services.web.sessions import require_dashboard_login
from services.web.test_data import activate_test_data, ensure_sample_data, record_sample_sync, sample_balances
from services.database.session import test_data_active
from services.validation import uid, optional_text
from services.banking.client import StarlingError, starling_request


dashboard = Blueprint("dashboard", __name__, url_prefix="/dashboard")
dashboard.before_request(require_dashboard_login)
dashboard.before_request(activate_test_data)


@dashboard.errorhandler(NotFound)
@dashboard.errorhandler(BadRequest)
def invalid_request(error):
    if request.endpoint in ("dashboard.account_balances", "dashboard.trigger_sync", "dashboard.sync_status"):
        return jsonify(error=error.description), 400
    return render_template("dashboard.html", error=error.description, page=None), error.code


@dashboard.errorhandler(SQLAlchemyError)
def database_error(error):
    db.session.rollback()
    log_failure("dashboard.database", error)
    message = "Unable to load transactions. Check PostgreSQL and run flask db-upgrade."
    if request.endpoint in ("dashboard.account_balances", "dashboard.trigger_sync", "dashboard.sync_status"):
        return jsonify(error="Unable to load saved accounts. Check PostgreSQL."), 503
    return render_template("dashboard.html", error=message, page=None), 503


@dashboard.post("/sync")
def trigger_sync():
    if not current_user.is_authenticated:
        return jsonify(error="Browser login is required."), 401
    options = query_values({"force"})
    if options.get("force", "0") not in ("0", "1"):
        raise BadRequest("force must be 0 or 1.")
    if test_data_active():
        if options.get('force') == '1':
            record_action('sync.sample')
        record_sample_sync()
        return jsonify(started=True, test_data=True), 202
    started = start_dashboard_sync(current_app._get_current_object(), force=options.get("force") == "1")
    if started and options.get('force') == '1':
        record_action('sync')
        db.session.commit()
    return jsonify(started=started), 202


@dashboard.get("/sync-status")
def sync_status():
    return jsonify(run=latest_sync())


def format_amount(amount_minor, currency):
    """Format common two-decimal currencies without converting money to floats."""
    if currency in ("GBP", "EUR", "USD"):
        sign = "−" if amount_minor < 0 else ""
        amount_minor = abs(amount_minor)
        return f"{sign}{currency} {amount_minor // 100:,}.{amount_minor % 100:02d}"
    # Other currencies can have different decimal scales, so preserve their units.
    return f"{currency} {amount_minor:,} minor units"


def requested_account(options):
    """Selecting an account filters the view without modifying saved records."""
    if options.get("account", "all") == "all":
        return None
    try:
        return uid(options["account"])
    except ValueError:
        raise BadRequest("account must be a valid account ID.") from None


def requested_page(options):
    """Keep pagination validation identical for the ledger and edit return links."""
    value = options.get("page", "1")
    if not re.fullmatch(r"[0-9]{1,10}", value) or not 1 <= int(value) <= 2**31 - 1:
        raise BadRequest("page must be a positive integer up to 2147483647.")
    return int(value)


@dashboard.get("/balances")
def account_balances():
    """Load live main-account balances separately from the saved transaction table."""
    if request.method == "HEAD":
        return jsonify(balances=[])

    account_uid = requested_account(query_values({"account"}))
    query = db.select(Account).options(
        load_only(Account.account_uid, Account.name, Account.currency)
    ).order_by(Account.account_uid)
    if account_uid:
        query = query.where(Account.account_uid == account_uid)
    accounts = list(db.session.scalars(query))
    if account_uid and not accounts:
        raise BadRequest("This account is not available. Choose a saved account.")
    if test_data_active():
        return jsonify(sample_balances(accounts, format_amount, combined=account_uid is None))
    balances = []
    totals = {}
    rate_limited = False
    for account in accounts:
        total = totals.setdefault(account.currency, {"minor_units": 0, "unavailable": False})
        card = {"name": account.name or f"Starling {account.currency} account",
                "amount": None, "error": None}
        if rate_limited:
            card["error"] = "Starling rate limit reached. Try again later."
        else:
            try:
                payload = starling_request(f"/api/v2/accounts/{account.account_uid}/balance", scope="balance:read")
                balance = payload.get("effectiveBalance") if isinstance(payload, dict) else None
                # Balances may be negative, unlike individual transaction amounts.
                if (not isinstance(balance, dict) or type(balance.get("minorUnits")) is not int
                        or not -(2**63) <= balance["minorUnits"] <= 2**63 - 1
                        or balance.get("currency") != account.currency):
                    raise StarlingError("Starling returned an invalid account balance.")
                card["amount"] = format_amount(balance["minorUnits"], balance["currency"])
                total["minor_units"] += balance["minorUnits"]
            except StarlingError as error:
                card["error"] = str(error)
                rate_limited = error.status_code == 429
        if card["error"]:
            total["unavailable"] = True
        balances.append(card)
    if account_uid:
        return jsonify(balances=balances)
    combined = [{
        "name": "Total balance · All accounts" if len(totals) == 1 else f"Total balance · All {currency} accounts",
        "amount": None if total["unavailable"] else format_amount(total["minor_units"], currency),
        "error": "Total unavailable: one or more account balances could not be loaded." if total["unavailable"] else None,
    } for currency, total in sorted(totals.items())]
    return jsonify(balances=balances, totals=combined)


@dashboard.get("")
def transactions_page():
    # 1. Read the requested page and reject invalid input before accessing PostgreSQL.
    options = query_values({"page", "account"})
    account_uid = requested_account(options)
    page_number = requested_page(options)

    accounts = list(db.session.scalars(db.select(Account).options(
        load_only(Account.account_uid, Account.name, Account.currency)
    ).order_by(Account.account_uid)))
    selected_account = next((item for item in accounts if item.account_uid == account_uid), None)
    if account_uid and selected_account is None:
        raise BadRequest("This account is not available. Choose a saved account.")

    # 2. Select only the fields this page displays, leaving private raw bank JSON unloaded.
    query = db.select(Transaction).options(
        load_only(Transaction.transaction_time, Transaction.counterparty_name,
                  Transaction.reference, Transaction.amount_minor, Transaction.currency,
                  Transaction.direction, Transaction.status, Transaction.source, Transaction.confirmed_at),
        selectinload(Transaction.classification), selectinload(Transaction.income),
    ).order_by(Transaction.transaction_time.desc(), Transaction.account_uid,
               Transaction.category_uid, Transaction.feed_item_uid)
    if account_uid:
        query = query.where(Transaction.account_uid == account_uid)
    page = db.paginate(query, page=page_number, per_page=50, error_out=False)

    # 3. Give the template plain display values; database work stays in this route.
    rows = []
    today = datetime.now(timezone.utc).date()
    for number, transaction in enumerate(page.items, start=(page_number - 1) * 50 + 1):
        classification = classification_details(transaction)
        transaction_date = transaction.transaction_time.astimezone(timezone.utc)
        rows.append({
            "number": number,
            "date": transaction_date.strftime("%d %b %Y"),
            "day": "Today" if transaction_date.date() == today else transaction_date.strftime("%A"),
            "counterparty": transaction.counterparty_name or "Unnamed payment",
            "reference": transaction.reference,
            "amount": format_amount(transaction.amount_minor, transaction.currency),
            "direction": transaction.direction,
            "status": transaction.status,
            "confirmed": getattr(transaction, "confirmed_at", None) is not None,
            "classification": CLASSIFICATION_TYPES[classification["type"]]["label"],
            "origin": classification["origin"],
            "income_url": url_for("dashboard.edit_income", account_uid=transaction.account_uid,
                                  category_uid=transaction.category_uid, feed_item_uid=transaction.feed_item_uid,
                                  account=account_uid, page=page_number) if classification["type"] == "income" else None,
            "income_status": "Needs review" if getattr(transaction, "income", None) and transaction.income.needs_review else
                             "Details saved" if getattr(transaction, "income", None) else "Details missing",
            "deduction_url": url_for('deductions.page', expense=f'{transaction.account_uid}:{transaction.category_uid}:{transaction.feed_item_uid}') if classification['type'] == 'expense' else None,
            "confirm_url": url_for("review.confirm_transaction", account_uid=transaction.account_uid,
                                   category_uid=transaction.category_uid, feed_item_uid=transaction.feed_item_uid),
            "edit_url": url_for("dashboard.edit_classification", account_uid=transaction.account_uid,
                                category_uid=transaction.category_uid, feed_item_uid=transaction.feed_item_uid,
                                account=account_uid, page=page_number),
        })

    return render_template("dashboard.html", page=page, rows=rows, error=None,
                           accounts=accounts, selected_account=selected_account, account_uid=account_uid,
                           current_user=current_user)


@dashboard.route("/transactions/<uuid:account_uid>/<uuid:category_uid>/<uuid:feed_item_uid>/classification", methods=["GET", "POST"])
def edit_classification(account_uid, category_uid, feed_item_uid):
    if not current_app.secret_key or len(current_app.secret_key) < 32:
        return render_template("dashboard.html", error="Set SECRET_KEY before using dashboard editing forms.", page=None), 503

    # 1. Keep return navigation constrained to our dashboard, never arbitrary URLs.
    options = query_values({"page", "account"})
    selected_account = requested_account(options)
    page_number = requested_page(options)
    back_url = url_for("dashboard.transactions_page", account=selected_account, page=page_number)

    # 2. Read the payment without its raw payload; lock the parent on saves.
    query = db.select(Transaction).where(
        Transaction.account_uid == str(account_uid), Transaction.category_uid == str(category_uid),
        Transaction.feed_item_uid == str(feed_item_uid)).options(
            load_only(Transaction.direction, Transaction.source, Transaction.amount_minor, Transaction.currency,
                      Transaction.counterparty_name, Transaction.reference),
            selectinload(Transaction.classification), selectinload(Transaction.income))
    if request.method == "POST":
        query = query.with_for_update(of=Transaction)
    transaction = db.session.scalar(query)
    if transaction is None:
        raise NotFound("Saved transaction not found.")
    details = classification_details(transaction)
    kind, notes = details["type"], details["notes"] or ""
    error = None

    # 3. Validate the form and use the same rules as the JSON API.
    if request.method == "POST":
        kind, notes = request.form.get("type", ""), request.form.get("notes", "")
        try:
            if set(request.form) - {"csrf_token", "type", "notes", "action"} or any(len(request.form.getlist(key)) != 1 for key in request.form):
                raise ValueError("Supply each form field once.")
            action = request.form.get("action", "save")
            if action == "automatic":
                clear_classification(transaction)
            elif action == "save":
                clean_notes = optional_text(notes, field="notes", maximum=2000)
                save_classification(transaction, kind, clean_notes)
            else:
                raise ValueError("Choose a valid action.")
        except ValueError as invalid:
            db.session.rollback()
            error = str(invalid)
        else:
            record_action('classification.' + action, str(feed_item_uid))
            db.session.commit()
            return redirect(back_url, code=303)

    choices = {key: value for key, value in CLASSIFICATION_TYPES.items()
               if transaction.direction in value["directions"]}
    return render_template("classification.html", transaction=transaction,
                           amount=format_amount(transaction.amount_minor, transaction.currency),
                           choices=choices, kind=kind, notes=notes, origin=details["origin"],
                           back_url=back_url, error=error), 400 if error else 200


@dashboard.route("/transactions/<uuid:account_uid>/<uuid:category_uid>/<uuid:feed_item_uid>/income", methods=["GET", "POST"])
def edit_income(account_uid, category_uid, feed_item_uid):
    # Browser edits always require a session and CSRF; API edits have their own route.
    if request.authorization is not None or not current_user.is_authenticated:
        return redirect(url_for("login.sign_in"))
    options = query_values({"page", "account", "return_to", "stream"})
    account = requested_account(options)
    page = requested_page(options)
    back_url = url_for("dashboard.transactions_page", account=account, page=page)
    if options.get('return_to') == 'income-streams':
        stream = options.get('stream', 'all')
        if stream != 'all':
            stream = stream_id(stream)
            if db.session.get(IncomeStream, stream) is None:
                raise NotFound('Income stream not found.')
        back_url = url_for('dashboard.income_streams', stream=stream if stream != 'all' else None, page=page)
    elif options.get('return_to') is not None or options.get('stream') is not None:
        raise BadRequest('Choose a valid return page.')
    query = db.select(Transaction).where(
        Transaction.account_uid == str(account_uid), Transaction.category_uid == str(category_uid),
        Transaction.feed_item_uid == str(feed_item_uid)).options(
            load_only(*(getattr(Transaction, field) for field in REVIEW_FIELDS)),
            selectinload(Transaction.classification), selectinload(Transaction.income))
    if request.method == "POST":
        query = query.with_for_update(of=Transaction)
    transaction = db.session.scalar(query)
    if transaction is None:
        raise NotFound("Saved transaction not found.")
    if transaction.direction != "IN" or classification_details(transaction)["type"] != "income":
        raise BadRequest("Income details require an incoming transaction classified as income.")
    transaction_version = review_version(transaction)
    record = transaction.income
    currency_changed = record is not None and record.recorded_currency != transaction.currency
    values = {field: '' for field in INCOME_FORM_FIELDS}
    values['tax_treatment'] = 'unknown'
    streams = db.session.scalars(db.select(IncomeStream).where(
        db.or_(IncomeStream.archived.is_(False), IncomeStream.id == record.income_stream_id if record else False)
    ).order_by(IncomeStream.name, IncomeStream.id)).all()
    if record:
        values.update(income_stream_id=record.income_stream_id or '', income_type=record.income_type, tax_treatment=record.tax_treatment,
                      source_name=record.source_name or '', adjustment_notes=record.adjustment_notes or '')
        if not currency_changed:
            for field, column in (('gross', 'gross_minor'), ('tax_deducted', 'tax_deducted_minor'), ('ni_deducted', 'ni_deducted_minor'), ('adjustment', 'adjustment_minor')):
                values[field] = display_amount(getattr(record, column), transaction.currency)
    error, error_status = None, 400
    original_values = dict(values)
    if request.method == "POST":
        values = {field: request.form.get(field, '') for field in INCOME_FORM_FIELDS}
        try:
            version = request.form.get('version', '')
            if not re.fullmatch(r'[0-9a-f]{64}', version) or not hmac.compare_digest(version, transaction_version):
                raise Conflict('The payment or income details changed while this form was open. Review the current values before saving.')
            chosen_id = stream_id(request.form.get('income_stream_id', ''))
            chosen = db.session.scalar(db.select(IncomeStream).where(IncomeStream.id == chosen_id).with_for_update().execution_options(populate_existing=True))
            if chosen is None or (chosen.archived and (not record or record.income_stream_id != chosen.id)):
                raise BadRequest('Choose an active income stream.')
            # Retain the existing API's historical income types; browser selection is a stream.
            kind = 'employment' if chosen.kind == 'employed' else 'other'
            clean = form_values(request.form, transaction.currency, income_type=kind)
            clean['income_stream_id'] = chosen.id
            action = request.form.get('action', 'save')
            if action != 'save':
                raise BadRequest('Choose a valid action.')
            save_income(transaction, clean)
            record_action('income.save', str(feed_item_uid))
            db.session.commit()
        except (BadRequest, Conflict) as invalid:
            db.session.rollback()
            error, error_status = invalid.description, invalid.code
            if isinstance(invalid, Conflict):
                values = original_values
        else:
            return redirect(back_url, code=303)
    return render_template('income.html', transaction=transaction, amount=format_amount(transaction.amount_minor, transaction.currency),
                           values=values, version=transaction_version, streams=streams, stream_kinds=STREAM_KINDS, treatments=TAX_TREATMENTS,
                           decimal_places=decimal_places(transaction.currency), currency_changed=currency_changed,
                           recorded_currency=record.recorded_currency if record else None,
                           needs_review=record.needs_review if record else False, back_url=back_url, error=error), error_status if error else 200


@dashboard.route('/income-streams', methods=['GET', 'POST'])
def income_streams():
    """Create and manage streams without changing bank records or inferring tax."""
    if request.authorization is not None or not current_user.is_authenticated:
        return redirect(url_for('login.sign_in'))
    options = query_values({'stream', 'page', 'create'})
    selected_id = stream_id(options['stream']) if options.get('stream') else None
    page_number = requested_page(options)
    if selected_id and db.session.get(IncomeStream, selected_id) is None:
        raise NotFound('Income stream not found.')
    error = None
    from services.transactions.mileage_relationships import mileage_partners, set_mileage_relationship
    create_fields = ('name', 'kind', 'expected_gross', 'expected_gross_period', 'expected_gross_currency', 'unpaid_holiday', 'unpaid_holiday_unit', 'forecast_tax_year', 'forecast_starts_on', 'forecast_ends_on', 'income_mode', 'mileage_with_stream')
    create_values = dict.fromkeys(create_fields, '')
    create_values.update(income_mode='forecast', expected_gross_period='yearly', expected_gross_currency='GBP', unpaid_holiday='0', unpaid_holiday_unit='weeks', forecast_tax_year='2026-27')
    editing_id, editing_values = None, {}
    if request.method == 'POST':
        submitted = {field: request.form.get(field, '') for field in create_fields}
        if request.form.get('action') == 'update':
            editing_id, editing_values = request.form.get('stream_id'), submitted
        else:
            create_values = submitted
        try:
            action, name, kind, forecast = stream_fields(request.form)
            # Coordinate relationship changes with mileage and shift writes.
            db.session.execute(db.select(db.func.pg_advisory_xact_lock(6075157141257144400 + int(test_data_active()))))
            if action == 'create':
                edited = IncomeStream(id=str(uuid4()), name=name, kind=kind, **forecast)
                db.session.add(edited)
            else:
                edited = db.session.scalar(db.select(IncomeStream).where(
                    IncomeStream.id == stream_id(request.form.get('stream_id', ''))).with_for_update().execution_options(populate_existing=True))
                if edited is None:
                    raise BadRequest('Income stream not found.')
                if action == 'update':
                    if kind != edited.kind:
                        raise BadRequest('Create a new stream to use a different type.')
                    if forecast['income_mode'] == 'shifts' and edited.expected_gross_minor is not None:
                        for field in ('expected_gross_minor', 'expected_gross_period', 'unpaid_holiday_weeks',
                                      'unpaid_holiday_unit', 'forecast_starts_on', 'forecast_ends_on'):
                            forecast[field] = getattr(edited, field)
                    existing_currencies = db.session.scalars(db.select(IncomeShift.currency).where(IncomeShift.income_stream_id == edited.id).distinct()).all()
                    if any(currency != forecast['expected_gross_currency'] for currency in existing_currencies):
                        raise BadRequest('Remove this stream’s shifts before changing their currency.')
                    edited.name = name
                    for field, value in forecast.items():
                        setattr(edited, field, value)
                else:
                    edited.archived = action == 'archive'
            if action == 'create' or (action == 'update' and 'mileage_with_stream' in request.form):
                set_mileage_relationship(edited, request.form.get('mileage_with_stream', ''))
            record_action('stream.' + action, edited.id)
            db.session.commit()
            return redirect(url_for('dashboard.income_streams', stream=selected_id, page=page_number), code=303)
        except BadRequest as invalid:
            db.session.rollback()
            error = invalid.description
    counts = dict(db.session.execute(db.select(TransactionIncome.income_stream_id, db.func.count())
                                    .group_by(TransactionIncome.income_stream_id)).all())
    streams = db.session.scalars(db.select(IncomeStream).order_by(IncomeStream.archived, IncomeStream.name, IncomeStream.id)).all()
    attach_shift_totals(streams)
    partners = mileage_partners(streams)
    names = {stream.id: stream.name for stream in streams}
    query = db.select(Transaction).join(Transaction.income).options(
        load_only(Transaction.transaction_time, Transaction.counterparty_name, Transaction.amount_minor, Transaction.currency),
        selectinload(Transaction.income)).order_by(Transaction.transaction_time.desc(), Transaction.account_uid,
                                                 Transaction.category_uid, Transaction.feed_item_uid)
    if selected_id:
        query = query.where(TransactionIncome.income_stream_id == selected_id)
    page = db.paginate(query, page=page_number, per_page=50, error_out=False)
    rows = [dict(source=payment.income.source_name or payment.counterparty_name or 'Unnamed source',
                 kind=names.get(payment.income.income_stream_id, 'No stream assigned'),
                 date=payment.transaction_time.strftime('%d %b %Y'),
                 amount=format_amount(payment.amount_minor, payment.currency), needs_review=payment.income.needs_review,
                 edit_url=url_for('dashboard.edit_income', account_uid=payment.account_uid,
                                  category_uid=payment.category_uid, feed_item_uid=payment.feed_item_uid,
                                  stream=selected_id or 'all', return_to='income-streams', page=page_number))
            for payment in page.items]
    return render_template('income_streams.html', streams=streams, mileage_partners=partners, selected_stream=selected_id,
                           counts=counts, stream_kinds=STREAM_KINDS, create_values=create_values, error=error,
                           forecast_periods=FORECAST_PERIODS, forecast_currencies=FORECAST_CURRENCIES,
                           tax_years=TAX_YEARS, active_days=active_days,
                           editing_id=editing_id, editing_values=editing_values,
                           display_amount=display_amount, format_amount=format_amount, annual_gross=annual_gross, holiday_amount=holiday_amount,
                           selected_name=names.get(selected_id, 'All income'), rows=rows, page=page,
                           current_user=current_user), 400 if error else 200


@dashboard.route('/tax-rules', methods=['GET', 'POST'])
def tax_rules():
    """Show reviewed rules and their official-source verification status."""
    from services.tax.rules import cached_status, reviewed_rules, refresh_rules, start_rule_check
    from services.tax import mileage_rules, ni_rules

    if request.authorization is not None or not current_user.is_authenticated:
        return redirect(url_for('login.sign_in'))
    query_values(set())
    if request.method == 'POST':
        if set(request.form) - {'csrf_token'} or any(len(request.form.getlist(key)) != 1 for key in request.form):
            raise BadRequest('Supply each form field once.')
        try:
            refresh_rules(current_app.instance_path, force=True)
            mileage_rules.refresh_rules(current_app.instance_path, force=True)
            ni_rules.refresh_rules(current_app.instance_path, force=True)
        except OSError:
            raise BadRequest('Unable to save the rule check. Check the instance directory permissions.') from None
        record_action('rules', sample=False)
        db.session.commit()
        return redirect(url_for('dashboard.tax_rules'), code=303)
    status = cached_status(current_app.instance_path)
    start_rule_check(current_app.instance_path)
    return render_template('tax_rules.html', rules=reviewed_rules(), status=status,
                           mileage_rules=mileage_rules.reviewed_rules(), mileage_status=mileage_rules.cached_status(current_app.instance_path),
                           ni_rules=ni_rules.reviewed_rules(), ni_status=ni_rules.cached_status(current_app.instance_path),
                           format_amount=format_amount, current_user=current_user)


@dashboard.get('/tax-estimate')
def tax_estimate():
    """Annual planning across all streams; never add bank receipts to forecasts."""
    from services.tax.estimate import estimate_streams, rounded_minor
    from services.tax.rules import reviewed_rules, cached_status, start_rule_check

    if request.authorization is not None or not current_user.is_authenticated:
        return redirect(url_for('login.sign_in'))
    options = query_values({'year', 'region'})
    if options.get('year', '2026-27') != '2026-27' or options.get('region', 'uk-ewni') != 'uk-ewni':
        raise BadRequest('The estimate currently supports England, Wales and Northern Ireland, 2026–27 only.')
    streams = db.session.scalars(db.select(IncomeStream).where(
        IncomeStream.forecast_tax_year == '2026-27').order_by(IncomeStream.name, IncomeStream.id)).all()
    start_rule_check(current_app.instance_path)
    rules = reviewed_rules()
    status = cached_status(current_app.instance_path)
    from services.tax.records import tax_records
    attach_shift_totals(streams)
    from services.tax import ni_rules
    records = tax_records(rules)
    records.setdefault('issues', [])
    if status.get('status') == 'needs_review':
        records['issues'].append('Income-tax rules changed and need review.')
    ni_status = ni_rules.cached_status(current_app.instance_path)
    if ni_status.get('status') == 'needs_review':
        records['issues'].append('National Insurance rules changed and need review.')
    estimate = estimate_streams(streams, rules, **records)
    return render_template('tax_estimate.html', estimate=estimate, rules=rules, status=status,
                           format_amount=format_amount, rounded_minor=rounded_minor, stream_kinds=STREAM_KINDS,
                           current_user=current_user)


@dashboard.post('/test-data')
def switch_test_data():
    """Explicit, CSRF-protected selection; always return to an unfiltered ledger."""
    if request.authorization is not None or not current_user.is_authenticated:
        return redirect(url_for('login.sign_in'))
    if (set(request.form) - {'csrf_token', 'enabled'} or
            any(len(request.form.getlist(key)) != 1 for key in request.form) or
            request.form.get('enabled') not in ('0', '1')):
        raise BadRequest('Choose whether test data is enabled.')
    enabled = request.form['enabled'] == '1'
    if enabled:
        from flask import g
        g.use_test_data = True
        ensure_sample_data()
    record_action('test-data.on' if enabled else 'test-data.off', sample=enabled)
    db.session.commit()
    session['use_test_data'] = enabled
    # Reject edit/confirm forms opened before a dataset switch in another tab.
    session.pop('csrf_token', None)
    return redirect(url_for('dashboard.transactions_page'), code=303)
