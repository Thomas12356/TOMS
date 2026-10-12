"""Flask-SQLAlchemy models mapped to the existing toms tables."""

from sqlalchemy.dialects.postgresql import JSONB

from services.database.connection import db


class BaseModel(db.Model):
    __abstract__ = True
    __table_args__ = {"schema": "toms"}

    def as_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class TaxProfile(BaseModel):
    __tablename__ = 'tax_profiles'
    __table_args__ = (
        db.CheckConstraint("tax_year = '2026-27'"),
        db.CheckConstraint("jurisdiction IN ('unknown', 'england', 'wales', 'northern_ireland', 'scotland', 'other')"),
        db.CheckConstraint('revision > 0'),
        db.CheckConstraint("(jurisdiction = 'unknown') = (confirmed_at IS NULL)"),
        {'schema': 'toms'},
    )

    tax_year = db.Column(db.Text, primary_key=True)
    jurisdiction = db.Column(db.Text, nullable=False, server_default='unknown')
    revision = db.Column(db.BigInteger, nullable=False, server_default='1')
    confirmed_at = db.Column(db.DateTime(timezone=True))


class CashCommitment(BaseModel):
    __tablename__ = 'cash_commitments'
    __table_args__ = (db.CheckConstraint("kind IN ('bill','hmrc')"),
        db.CheckConstraint('amount_minor >= 0 AND revision > 0'),
        db.CheckConstraint("recurrence IN ('once','weekly','monthly','yearly')"),
        db.CheckConstraint('end_date IS NULL OR end_date >= due_date'),
        db.CheckConstraint("kind = 'bill' OR (recurrence = 'once' AND currency = 'GBP' AND tax_year IS NOT NULL AND tax_component IS NOT NULL)"),
        db.CheckConstraint("kind = 'hmrc' OR (tax_year IS NULL AND tax_component IS NULL)"), {'schema':'toms'})
    id = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    kind = db.Column(db.Text, nullable=False)
    name = db.Column(db.Text, nullable=False)
    currency = db.Column(db.Text, nullable=False)
    amount_minor = db.Column(db.BigInteger)
    due_date = db.Column(db.Date, nullable=False)
    recurrence = db.Column(db.Text, nullable=False)
    end_date = db.Column(db.Date)
    tax_year = db.Column(db.Text)
    tax_component = db.Column(db.Text)
    notes = db.Column(db.Text, nullable=False, default='')
    active = db.Column(db.Boolean, nullable=False, default=True)
    revision = db.Column(db.BigInteger, nullable=False, default=1)


class CashPayment(BaseModel):
    __tablename__ = 'cash_payments'
    __table_args__ = (db.CheckConstraint('amount_minor > 0'),
        db.CheckConstraint('tax_credit_minor >= 0 AND tax_credit_minor <= amount_minor'), {'schema':'toms'})
    id = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    commitment_id = db.Column(db.Uuid(as_uuid=False), db.ForeignKey('toms.cash_commitments.id'), nullable=False)
    occurrence_date = db.Column(db.Date, nullable=False)
    amount_minor = db.Column(db.BigInteger, nullable=False)
    tax_credit_minor = db.Column(db.BigInteger, nullable=False, default=0, server_default='0')
    paid_on = db.Column(db.Date, nullable=False)
    reference = db.Column(db.Text, nullable=False, default='')
    active = db.Column(db.Boolean, nullable=False, default=True)
    recorded_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    changed_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())


class CashBalance(BaseModel):
    __tablename__ = 'cash_balances'
    __table_args__ = (db.CheckConstraint('revision > 0'), {'schema':'toms'})
    account_uid = db.Column(db.Uuid(as_uuid=False), db.ForeignKey('toms.accounts.account_uid', ondelete='CASCADE'), primary_key=True)
    amount_minor = db.Column(db.BigInteger, nullable=False)
    recorded_currency = db.Column(db.Text, nullable=False)
    payment_fingerprint = db.Column(db.Text)
    observed_at = db.Column(db.DateTime(timezone=True), nullable=False)
    recorded_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    revision = db.Column(db.BigInteger, nullable=False, default=1)


class CashCoverage(BaseModel):
    __tablename__ = 'cash_coverage'
    __table_args__ = (db.CheckConstraint('id = 1'), {'schema':'toms'})
    id = db.Column(db.Integer, primary_key=True)
    fingerprint = db.Column(db.Text, nullable=False)
    confirmed_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())


class Account(BaseModel):
    __tablename__ = "accounts"
    __table_args__ = (db.CheckConstraint("provider IN ('starling','statement')"),
                      db.CheckConstraint('revision > 0'), {'schema':'toms'})

    account_uid = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    default_category_uid = db.Column(db.Uuid(as_uuid=False), nullable=False)
    currency = db.Column(db.Text, nullable=False)
    name = db.Column(db.Text)
    account_type = db.Column(db.Text)
    opened_at = db.Column(db.DateTime(timezone=True))
    raw_payload = db.Column(JSONB, nullable=False)
    fetched_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    provider = db.Column(db.Text, nullable=False, default='starling', server_default='starling')
    bank_name = db.Column(db.Text)
    cash_active = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    closed_on = db.Column(db.Date)
    revision = db.Column(db.BigInteger, nullable=False, default=1, server_default='1')


class AccountHistory(BaseModel):
    __tablename__ = 'account_history'
    __table_args__ = (db.CheckConstraint("tax_year = '2026-27'"),
        db.CheckConstraint('through_on >= from_on AND revision > 0'), {'schema':'toms'})
    account_uid = db.Column(db.Uuid(as_uuid=False), db.ForeignKey('toms.accounts.account_uid'), primary_key=True)
    tax_year = db.Column(db.Text, primary_key=True)
    from_on = db.Column(db.Date, nullable=False)
    through_on = db.Column(db.Date, nullable=False)
    complete = db.Column(db.Boolean, nullable=False, default=False)
    revision = db.Column(db.BigInteger, nullable=False, default=1)
    confirmed_at = db.Column(db.DateTime(timezone=True))


class StatementImport(BaseModel):
    __tablename__ = 'statement_imports'
    __table_args__ = (db.CheckConstraint("status IN ('mapping','preview','imported','discarded')"),
        db.CheckConstraint('account_revision > 0 AND row_count BETWEEN 0 AND 1000'),
        db.UniqueConstraint('id','account_uid'), {'schema':'toms'})
    id = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    account_uid = db.Column(db.Uuid(as_uuid=False), db.ForeignKey('toms.accounts.account_uid'), nullable=False)
    account_revision = db.Column(db.BigInteger, nullable=False)
    file_hash = db.Column(db.Text, nullable=False)
    mapping_hash = db.Column(db.Text)
    mapping = db.Column(JSONB)
    headers = db.Column(JSONB, nullable=False, default=list)
    rows = db.Column(JSONB, nullable=False, default=list)
    status = db.Column(db.Text, nullable=False, default='mapping')
    row_count = db.Column(db.Integer, nullable=False)
    imported_count = db.Column(db.Integer, nullable=False, default=0)
    skipped_count = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    imported_at = db.Column(db.DateTime(timezone=True))


class StatementImportRow(BaseModel):
    __tablename__ = 'statement_import_rows'
    __table_args__ = (db.ForeignKeyConstraint(['batch_id','account_uid'],['toms.statement_imports.id','toms.statement_imports.account_uid']),
        db.ForeignKeyConstraint(['account_uid','category_uid','feed_item_uid'],['toms.transactions.account_uid','toms.transactions.category_uid','toms.transactions.feed_item_uid']),
        db.CheckConstraint("decision IN ('import','skip') AND row_number > 0"), {'schema':'toms'})
    batch_id = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    row_number = db.Column(db.Integer, primary_key=True)
    account_uid = db.Column(db.Uuid(as_uuid=False), nullable=False)
    category_uid = db.Column(db.Uuid(as_uuid=False))
    feed_item_uid = db.Column(db.Uuid(as_uuid=False))
    decision = db.Column(db.Text, nullable=False)


class EvidenceAsset(BaseModel):
    __tablename__ = 'evidence_assets'
    __table_args__ = (db.CheckConstraint('byte_count BETWEEN 1 AND 10485760'), {'schema':'toms'})
    sha256 = db.Column(db.Text, primary_key=True)
    byte_count = db.Column(db.Integer, nullable=False)
    mime = db.Column(db.Text, nullable=False)
    suffix = db.Column(db.Text, nullable=False)
    data = db.deferred(db.Column(db.LargeBinary, nullable=False))
    preview = db.deferred(db.Column(db.LargeBinary))
    pages = db.Column(db.Integer)


class EvidenceDocument(BaseModel):
    __tablename__ = 'evidence_documents'
    __table_args__ = (db.ForeignKeyConstraint(['account_uid','category_uid','feed_item_uid'],
        ['toms.transactions.account_uid','toms.transactions.category_uid','toms.transactions.feed_item_uid']),
        db.CheckConstraint("(kind='transaction' AND account_uid IS NOT NULL AND category_uid IS NOT NULL AND feed_item_uid IS NOT NULL AND mileage_id IS NULL) OR (kind='mileage' AND account_uid IS NULL AND category_uid IS NULL AND feed_item_uid IS NULL)"),
        db.CheckConstraint('revision > 0 AND char_length(name) BETWEEN 1 AND 200 AND char_length(notes) <= 1000'),
        {'schema':'toms'})
    id = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    asset_sha256 = db.Column(db.Text, db.ForeignKey('toms.evidence_assets.sha256'), nullable=False)
    kind = db.Column(db.Text, nullable=False)
    account_uid = db.Column(db.Uuid(as_uuid=False))
    category_uid = db.Column(db.Uuid(as_uuid=False))
    feed_item_uid = db.Column(db.Uuid(as_uuid=False))
    mileage_id = db.Column(db.Uuid(as_uuid=False), db.ForeignKey('toms.mileage_entries.id',ondelete='SET NULL'))
    name = db.Column(db.Text, nullable=False)
    notes = db.Column(db.Text, nullable=False, default='')
    active = db.Column(db.Boolean, nullable=False, default=True)
    revision = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    archived_at = db.Column(db.DateTime(timezone=True))
    asset = db.relationship('EvidenceAsset')


class StatementMapping(BaseModel):
    __tablename__ = 'statement_mappings'
    __table_args__ = ({'schema': 'toms'},)
    account_uid = db.Column(db.Uuid(as_uuid=False), db.ForeignKey('toms.accounts.account_uid'), primary_key=True)
    signature = db.Column(db.Text, primary_key=True)
    mapping = db.Column(JSONB, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now(), onupdate=db.func.now())


class StatementChange(BaseModel):
    __tablename__ = 'statement_changes'
    __table_args__ = ({'schema': 'toms'},)
    id = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    account_uid = db.Column(db.Uuid(as_uuid=False), db.ForeignKey('toms.accounts.account_uid'), nullable=False)
    kind = db.Column(db.Text, nullable=False)
    before = db.Column(JSONB, nullable=False)
    after = db.Column(JSONB, nullable=False)
    reason = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())


class Category(BaseModel):
    __tablename__ = "categories"
    __table_args__ = (
        db.CheckConstraint("kind IN ('main', 'savings', 'spending', 'manual')"),
        {"schema": "toms"},
    )

    account_uid = db.Column(db.Uuid(as_uuid=False), db.ForeignKey("toms.accounts.account_uid"), primary_key=True)
    category_uid = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    name = db.Column(db.Text)
    kind = db.Column(db.Text, nullable=False)
    history_from = db.Column(db.DateTime(timezone=True))
    history_through = db.Column(db.DateTime(timezone=True))
    changes_through = db.Column(db.DateTime(timezone=True))


class Transaction(BaseModel):
    __tablename__ = "transactions"
    __table_args__ = (
        db.ForeignKeyConstraint(["account_uid", "category_uid"],
                                ["toms.categories.account_uid", "toms.categories.category_uid"]),
        db.CheckConstraint("amount_minor >= 0"),
        db.CheckConstraint("source_amount_minor >= 0"),
        db.CheckConstraint("direction IN ('IN', 'OUT')"),
        db.Index("transactions_account_time", "account_uid", "transaction_time"),
        db.Index("transactions_status", "status"),
        {"schema": "toms"},
    )

    account_uid = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    category_uid = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    feed_item_uid = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    amount_minor = db.Column(db.BigInteger, nullable=False)
    currency = db.Column(db.Text, nullable=False)
    direction = db.Column(db.Text, nullable=False)
    status = db.Column(db.Text, nullable=False)
    transaction_time = db.Column(db.DateTime(timezone=True), nullable=False)
    source_updated_at = db.Column(db.DateTime(timezone=True), nullable=False)
    settlement_time = db.Column(db.DateTime(timezone=True))
    confirmed_at = db.Column(db.DateTime(timezone=True))
    source = db.Column(db.Text)
    spending_category = db.Column(db.Text)
    counterparty_name = db.Column(db.Text)
    reference = db.Column(db.Text)
    source_amount_minor = db.Column(db.BigInteger)
    source_currency = db.Column(db.Text)
    raw_payload = db.Column(JSONB, nullable=False)
    fetched_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())

    expense = db.relationship("ExpenseDeduction", uselist=False, back_populates="transaction",
        cascade="all, delete-orphan", passive_deletes=True)

    income = db.relationship("TransactionIncome", uselist=False,
        back_populates="transaction", cascade="all, delete-orphan", passive_deletes=True)

    classification = db.relationship("TransactionClassification", uselist=False,
        back_populates="transaction", cascade="all, delete-orphan", passive_deletes=True)


class TransactionClassification(BaseModel):
    __tablename__ = "transaction_classifications"
    __table_args__ = (
        db.ForeignKeyConstraint(["account_uid", "category_uid", "feed_item_uid"],
            ["toms.transactions.account_uid", "toms.transactions.category_uid", "toms.transactions.feed_item_uid"],
            ondelete="CASCADE"),
        db.CheckConstraint("type IN ('income', 'expense', 'internal_transfer', 'refund', 'other')"),
        db.CheckConstraint("char_length(notes) <= 2000"),
        {"schema": "toms"},
    )

    account_uid = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    category_uid = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    feed_item_uid = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    type = db.Column(db.Text, nullable=False)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False,
                           server_default=db.func.now(), onupdate=db.func.now())
    transaction = db.relationship("Transaction", back_populates="classification")


class IncomeStream(BaseModel):
    __tablename__ = "income_streams"
    __table_args__ = (
        db.CheckConstraint("char_length(trim(name)) BETWEEN 1 AND 200"),
        db.CheckConstraint("kind IN ('self_employed', 'employed', 'cis')"),
        db.CheckConstraint("income_mode IN ('forecast', 'shifts')"),
        db.CheckConstraint("(expected_gross_minor IS NULL AND expected_gross_period IS NULL AND expected_gross_currency IS NULL) OR "
                           "(expected_gross_minor IS NOT NULL AND expected_gross_minor >= 0 AND "
                           "expected_gross_period IS NOT NULL AND expected_gross_period IN ('weekly', 'monthly', 'yearly') AND "
                           "expected_gross_currency IS NOT NULL AND expected_gross_currency IN ('GBP', 'EUR', 'USD'))",
                           name="income_stream_forecast_complete"),
        db.CheckConstraint("unpaid_holiday_weeks BETWEEN 0 AND 52", name="income_stream_holiday_range"),
        db.CheckConstraint("unpaid_holiday_unit IN ('days', 'weeks')", name="income_stream_holiday_unit"),
        db.CheckConstraint("forecast_tax_year = '2026-27'", name="income_stream_forecast_year"),
        db.CheckConstraint("forecast_starts_on IS NULL OR forecast_ends_on IS NULL OR forecast_starts_on <= forecast_ends_on",
                           name="income_stream_forecast_dates"),
        {"schema": "toms"},
    )

    id = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    name = db.Column(db.Text, nullable=False)
    kind = db.Column(db.Text, nullable=False)
    income_mode = db.Column(db.Text, nullable=False, server_default="forecast")
    archived = db.Column(db.Boolean, nullable=False, server_default=db.false())
    expected_gross_minor = db.Column(db.BigInteger)
    expected_gross_period = db.Column(db.Text)
    expected_gross_currency = db.Column(db.Text)
    unpaid_holiday_weeks = db.Column(db.Numeric(6, 4), nullable=False, server_default="0")
    unpaid_holiday_unit = db.Column(db.Text, nullable=False, server_default="weeks")
    forecast_tax_year = db.Column(db.Text, nullable=False, server_default="2026-27")
    forecast_starts_on = db.Column(db.Date)
    forecast_ends_on = db.Column(db.Date)
    # Shared by sources belonging to the same business/associated employment.
    mileage_pool_id = db.Column(db.Uuid(as_uuid=False), nullable=False,
                                server_default=db.func.gen_random_uuid(), index=True)


class TransactionIncome(BaseModel):
    __tablename__ = "transaction_income"
    __table_args__ = (
        db.ForeignKeyConstraint(["account_uid", "category_uid", "feed_item_uid"],
            ["toms.transactions.account_uid", "toms.transactions.category_uid", "toms.transactions.feed_item_uid"],
            ondelete="CASCADE"),
        db.CheckConstraint("income_type IN ('roofing', 'amazon_flex', 'employment', 'other', 'personal_gift', 'inheritance', 'loan_received', 'loan_repayment', 'tax_refund', 'personal_item_sale', 'tax_free_benefit')"),
        db.CheckConstraint("tax_treatment IN ('unknown', 'no_tax_deducted', 'cis', 'paye', 'other_deduction', 'non_taxable')"),
        db.CheckConstraint("char_length(source_name) <= 200"),
        db.CheckConstraint("adjustment_minor = 0 OR (adjustment_notes IS NOT NULL AND char_length(trim(adjustment_notes)) > 0)", name="income_adjustment_explained"),
        db.CheckConstraint("char_length(adjustment_notes) <= 2000", name="income_adjustment_notes_length"),
        db.CheckConstraint("gross_minor >= 0"),
        db.CheckConstraint("tax_deducted_minor >= 0"),
        db.CheckConstraint("ni_deducted_minor >= 0"),
        db.CheckConstraint("COALESCE(tax_deducted_minor, 0)::numeric + COALESCE(ni_deducted_minor, 0)::numeric <= gross_minor", name="income_total_deductions"),
        db.CheckConstraint("tax_deducted_minor <= gross_minor"),
        db.CheckConstraint("tax_treatment <> 'unknown' OR tax_deducted_minor IS NULL"),
        db.CheckConstraint("tax_treatment <> 'non_taxable' OR tax_deducted_minor IS NULL OR tax_deducted_minor = 0", name="income_non_taxable_zero_tax"),
        db.CheckConstraint("tax_treatment <> 'no_tax_deducted' OR tax_deducted_minor IS NULL OR tax_deducted_minor = 0"),
        {"schema": "toms"},
    )

    account_uid = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    category_uid = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    feed_item_uid = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    income_stream_id = db.Column(db.Uuid(as_uuid=False), db.ForeignKey("toms.income_streams.id"), index=True)
    income_stream = db.relationship("IncomeStream")
    income_type = db.Column(db.Text, nullable=False)
    tax_treatment = db.Column(db.Text, nullable=False)
    source_name = db.Column(db.Text)
    gross_minor = db.Column(db.BigInteger)
    tax_deducted_minor = db.Column(db.BigInteger)
    ni_deducted_minor = db.Column(db.BigInteger)
    adjustment_minor = db.Column(db.BigInteger, nullable=False, server_default="0")
    adjustment_notes = db.Column(db.Text)
    recorded_currency = db.Column(db.Text, nullable=False)
    needs_review = db.Column(db.Boolean, nullable=False, server_default=db.false())
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False,
                           server_default=db.func.now(), onupdate=db.func.now())
    transaction = db.relationship("Transaction", back_populates="income")


class SyncRun(BaseModel):
    __tablename__ = "sync_runs"
    __table_args__ = (
        db.CheckConstraint("status IN ('running', 'completed', 'failed')"),
        {"schema": "toms"},
    )

    run_uid = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    status = db.Column(db.Text, nullable=False)
    requested_options = db.Column(JSONB, nullable=False)
    snapshot_at = db.Column(db.DateTime(timezone=True), nullable=False)
    started_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    finished_at = db.Column(db.DateTime(timezone=True))
    pages_committed = db.Column(db.Integer, nullable=False, server_default="0")
    items_received = db.Column(db.Integer, nullable=False, server_default="0")
    rows_changed = db.Column(db.Integer, nullable=False, server_default="0")
    error = db.Column(db.Text)
    targets = db.relationship("SyncTarget", back_populates="run",
                              order_by="(SyncTarget.account_uid, SyncTarget.category_uid)")


class SyncTarget(BaseModel):
    __tablename__ = "sync_targets"
    __table_args__ = (
        db.ForeignKeyConstraint(["account_uid", "category_uid"],
                                ["toms.categories.account_uid", "toms.categories.category_uid"]),
        db.CheckConstraint("status IN ('running', 'completed', 'failed')"),
        db.CheckConstraint("mode IN ('history', 'incremental')"),
        {"schema": "toms"},
    )

    run_uid = db.Column(db.Uuid(as_uuid=False), db.ForeignKey("toms.sync_runs.run_uid"), primary_key=True)
    account_uid = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    category_uid = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    mode = db.Column(db.Text, nullable=False)
    requested_start = db.Column(db.DateTime(timezone=True), nullable=False)
    requested_end = db.Column(db.DateTime(timezone=True), nullable=False)
    status = db.Column(db.Text, nullable=False)
    pages_committed = db.Column(db.Integer, nullable=False, server_default="0")
    items_received = db.Column(db.Integer, nullable=False, server_default="0")
    rows_changed = db.Column(db.Integer, nullable=False, server_default="0")
    earliest_received = db.Column(db.DateTime(timezone=True))
    latest_received = db.Column(db.DateTime(timezone=True))
    run = db.relationship("SyncRun", back_populates="targets")


class OwnerLogin(BaseModel):
    """Only row 1 is allowed: this application has a single owner."""
    __tablename__ = "owner_login"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.Text, nullable=False)
    password_hash = db.Column(db.Text, nullable=False)


class BrowserSession(BaseModel):
    __tablename__ = "browser_sessions"
    token_hash = db.Column(db.Text, primary_key=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False)
    last_seen_at = db.Column(db.DateTime(timezone=True), nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)


class LoginAttempt(BaseModel):
    """Map the existing atomic throttle so test schema routing applies to it."""
    __tablename__ = 'login_attempts'
    __table_args__ = (db.CheckConstraint('id = 1'), {'schema': 'toms'})
    id = db.Column(db.Integer, primary_key=True)
    window_started_at = db.Column(db.DateTime(timezone=True), nullable=False)
    attempts = db.Column(db.Integer, nullable=False)


class OwnerSetup(BaseModel):
    __tablename__ = "owner_setup"
    id = db.Column(db.Integer, primary_key=True)
    token_hash = db.Column(db.Text, nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)


class ExpenseDeduction(BaseModel):
    """A linked bank expense, optionally claimed against one income stream."""
    __tablename__ = 'expense_deductions'
    __table_args__ = (
        db.ForeignKeyConstraint(['shift_id', 'income_stream_id'], ['toms.income_shifts.id', 'toms.income_shifts.income_stream_id']),
        db.ForeignKeyConstraint(['account_uid', 'category_uid', 'feed_item_uid'],
            ['toms.transactions.account_uid', 'toms.transactions.category_uid', 'toms.transactions.feed_item_uid'], ondelete='CASCADE'),
        db.CheckConstraint('amount_minor > 0 AND amount_minor <= recorded_amount_minor'),
        db.CheckConstraint("char_length(trim(purpose)) BETWEEN 1 AND 1000"),
        db.CheckConstraint("category IN ('general','vehicle_running','parking_tolls')"),
        db.CheckConstraint("char_length(vehicle_key) <= 40 AND (category <> 'vehicle_running' OR char_length(trim(vehicle_key)) > 0)"),
        db.CheckConstraint('evidence_needed_reason IS NULL OR char_length(trim(evidence_needed_reason)) BETWEEN 1 AND 1000'),
        {'schema': 'toms'},
    )
    account_uid = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    category_uid = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    feed_item_uid = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    income_stream_id = db.Column(db.Uuid(as_uuid=False), db.ForeignKey('toms.income_streams.id'), nullable=False, index=True)
    shift_id = db.Column(db.Uuid(as_uuid=False), index=True)
    is_tax_deductible = db.Column(db.Boolean, nullable=False, server_default=db.true())
    amount_minor = db.Column(db.BigInteger, nullable=False)
    purpose = db.Column(db.Text, nullable=False)
    category = db.Column(db.Text, nullable=False)
    vehicle_key = db.Column(db.Text, nullable=False, server_default='')
    recorded_amount_minor = db.Column(db.BigInteger, nullable=False)
    recorded_currency = db.Column(db.Text, nullable=False)
    recorded_time = db.Column(db.DateTime(timezone=True), nullable=False)
    evidence_needed_reason = db.Column(db.Text)
    transaction = db.relationship('Transaction', back_populates='expense')


class MileageEntry(BaseModel):
    """An actual journey; allowance is calculated across the year, not per row."""
    __tablename__ = 'mileage_entries'
    __table_args__ = (
        db.ForeignKeyConstraint(['shift_id', 'income_stream_id'], ['toms.income_shifts.id', 'toms.income_shifts.income_stream_id']),
        db.CheckConstraint("journey_date BETWEEN '2026-04-06' AND '2027-04-05'"),
        db.CheckConstraint("location IN ('england','wales','northern_ireland','scotland')"),
        db.CheckConstraint("vehicle_type IN ('car_van','motorcycle','bicycle')"),
        db.CheckConstraint("char_length(trim(vehicle_key)) BETWEEN 1 AND 40"),
        db.CheckConstraint('miles > 0 AND miles <= 100000'),
        db.CheckConstraint("char_length(trim(purpose)) BETWEEN 1 AND 1000"),
        db.CheckConstraint("char_length(trim(start_postcode)) BETWEEN 1 AND 12 AND char_length(trim(end_postcode)) BETWEEN 1 AND 12"),
        db.CheckConstraint('reimbursed_minor >= 0'),
        db.CheckConstraint('evidence_needed_reason IS NULL OR char_length(trim(evidence_needed_reason)) BETWEEN 1 AND 1000'),
        {'schema': 'toms'},
    )
    id = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    income_stream_id = db.Column(db.Uuid(as_uuid=False), db.ForeignKey('toms.income_streams.id'), nullable=False, index=True)
    income_stream = db.relationship('IncomeStream')
    shift_id = db.Column(db.Uuid(as_uuid=False), index=True)
    journey_date = db.Column(db.Date, nullable=False)
    location = db.Column(db.Text, nullable=False)
    vehicle_type = db.Column(db.Text, nullable=False)
    vehicle_key = db.Column(db.Text, nullable=False)
    miles = db.Column(db.Numeric(9, 2), nullable=False)
    purpose = db.Column(db.Text, nullable=False)
    start_postcode = db.Column(db.Text, nullable=False)
    end_postcode = db.Column(db.Text, nullable=False)
    reimbursed_minor = db.Column(db.BigInteger, nullable=False, server_default='0')
    evidence_needed_reason = db.Column(db.Text)


class IncomeShift(BaseModel):
    """One planned or completed shift, before tax; not a second bank payment."""
    __tablename__ = 'income_shifts'
    __table_args__ = (
        db.UniqueConstraint('id', 'income_stream_id'),
        db.CheckConstraint("ends_at > starts_at AND ends_at - starts_at <= interval '24 hours'"),
        db.CheckConstraint("unpaid_break_minutes >= 0 AND unpaid_break_minutes * interval '1 minute' < ends_at - starts_at"),
        db.CheckConstraint("payment_mode IN ('hourly','total')"),
        db.CheckConstraint("(payment_mode = 'hourly' AND hourly_rate_minor IS NOT NULL AND hourly_rate_minor > 0) OR (payment_mode = 'total' AND hourly_rate_minor IS NULL)"),
        db.CheckConstraint('gross_minor > 0'),
        db.CheckConstraint("currency IN ('GBP','EUR','USD')"),
        db.CheckConstraint('char_length(notes) <= 1000'),
        {'schema': 'toms'},
    )
    id = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    income_stream_id = db.Column(db.Uuid(as_uuid=False), db.ForeignKey('toms.income_streams.id'), nullable=False, index=True)
    starts_at = db.Column(db.DateTime(timezone=True), nullable=False)
    ends_at = db.Column(db.DateTime(timezone=True), nullable=False)
    unpaid_break_minutes = db.Column(db.Integer, nullable=False, server_default='0')
    payment_mode = db.Column(db.Text, nullable=False)
    is_overtime = db.Column(db.Boolean, nullable=False, server_default=db.false())
    hourly_rate_minor = db.Column(db.BigInteger)
    gross_minor = db.Column(db.BigInteger, nullable=False)
    currency = db.Column(db.Text, nullable=False)
    notes = db.Column(db.Text, nullable=False, server_default='')


class UserAction(BaseModel):
    """Append-only owner activity, shared across real and sample modes."""
    __tablename__ = 'user_actions'
    id = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    occurred_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    action = db.Column(db.Text, nullable=False)
    sample_data = db.Column(db.Boolean, nullable=False, server_default=db.false())
    record_id = db.Column(db.Text, nullable=False, server_default='')


class Invoice(BaseModel):
    __tablename__ = 'invoices'
    __table_args__ = (
        db.CheckConstraint("state IN ('draft','issued','cancelled')"),
        db.CheckConstraint('revision > 0 AND total_minor > 0'),
        db.CheckConstraint('char_length(trim(client_name)) BETWEEN 1 AND 200'),
        db.CheckConstraint('char_length(client_address) <= 2000 AND char_length(reference) <= 200'),
        db.CheckConstraint('char_length(business_name) <= 200 AND char_length(business_address) <= 2000'),
        db.CheckConstraint('char_length(business_contact) <= 500 AND char_length(payment_instructions) <= 2000'),
        db.CheckConstraint("state <> 'draft' OR (number IS NULL AND issued_at IS NULL)"),
        db.CheckConstraint("state <> 'issued' OR (number IS NOT NULL AND issued_at IS NOT NULL)"),
        db.CheckConstraint("state <> 'cancelled' OR cancelled_at IS NOT NULL"),
        db.CheckConstraint('due_date >= issue_date'),
        db.CheckConstraint("currency IN ('GBP','EUR','USD')"),
        {'schema': 'toms'},
    )
    id = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    income_stream_id = db.Column(db.Uuid(as_uuid=False), db.ForeignKey('toms.income_streams.id'), nullable=False)
    stream_name = db.Column(db.Text, nullable=False)
    number = db.Column(db.Text, unique=True)
    state = db.Column(db.Text, nullable=False, server_default='draft')
    revision = db.Column(db.Integer, nullable=False, server_default='1')
    client_name = db.Column(db.Text, nullable=False)
    client_address = db.Column(db.Text, nullable=False, server_default='')
    reference = db.Column(db.Text, nullable=False, server_default='')
    business_name = db.Column(db.Text, nullable=False, server_default='')
    business_address = db.Column(db.Text, nullable=False, server_default='')
    business_contact = db.Column(db.Text, nullable=False, server_default='')
    payment_instructions = db.Column(db.Text, nullable=False, server_default='')
    issue_date = db.Column(db.Date, nullable=False)
    due_date = db.Column(db.Date, nullable=False)
    currency = db.Column(db.Text, nullable=False)
    total_minor = db.Column(db.BigInteger, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    issued_at = db.Column(db.DateTime(timezone=True))
    cancelled_at = db.Column(db.DateTime(timezone=True))
    items = db.relationship('InvoiceItem', back_populates='invoice', lazy='selectin')
    payments = db.relationship('InvoicePayment', back_populates='invoice', lazy='selectin')
    deliveries = db.relationship('InvoiceDelivery', lazy='selectin')


class InvoiceDelivery(BaseModel):
    """Owner-recorded sending history; records do not send messages."""
    __tablename__ = 'invoice_deliveries'
    __table_args__ = (
        db.CheckConstraint("method IN ('email', 'post', 'hand', 'other')"),
        db.CheckConstraint('char_length(trim(recipient)) BETWEEN 1 AND 500'),
        db.CheckConstraint('char_length(notes) <= 1000'),
        {'schema': 'toms'},
    )
    id = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    invoice_id = db.Column(db.Uuid(as_uuid=False), db.ForeignKey('toms.invoices.id'), nullable=False)
    sent_at = db.Column(db.DateTime(timezone=True), nullable=False)
    method = db.Column(db.Text, nullable=False)
    recipient = db.Column(db.Text, nullable=False)
    notes = db.Column(db.Text, nullable=False, server_default='')
    recorded_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())


class InvoiceItem(BaseModel):
    __tablename__ = 'invoice_items'
    __table_args__ = (
        db.CheckConstraint('amount_minor > 0'),
        db.CheckConstraint('NOT active OR shift_id IS NOT NULL'),
        db.Index('invoice_items_active_shift', 'shift_id', unique=True, postgresql_where=db.text('active')),
        {'schema': 'toms'},
    )
    id = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    invoice_id = db.Column(db.Uuid(as_uuid=False), db.ForeignKey('toms.invoices.id', ondelete='CASCADE'), nullable=False)
    shift_id = db.Column(db.Uuid(as_uuid=False), db.ForeignKey('toms.income_shifts.id', ondelete='SET NULL'))
    active = db.Column(db.Boolean, nullable=False, server_default=db.true())
    description = db.Column(db.Text, nullable=False)
    amount_minor = db.Column(db.BigInteger, nullable=False)
    source_version = db.Column(db.Text, nullable=False)
    shift_snapshot = db.Column(JSONB, nullable=False)
    invoice = db.relationship('Invoice', back_populates='items')


class InvoicePayment(BaseModel):
    __tablename__ = 'invoice_payments'
    __table_args__ = (
        db.ForeignKeyConstraint(['account_uid', 'category_uid', 'feed_item_uid'],
            ['toms.transactions.account_uid', 'toms.transactions.category_uid', 'toms.transactions.feed_item_uid']),
        db.CheckConstraint('cash_minor >= 0 AND tax_minor >= 0 AND ni_minor >= 0'),
        db.CheckConstraint('cash_minor::numeric + tax_minor::numeric + ni_minor::numeric > 0'),
        {'schema': 'toms'},
    )
    id = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    invoice_id = db.Column(db.Uuid(as_uuid=False), db.ForeignKey('toms.invoices.id'), nullable=False)
    account_uid = db.Column(db.Uuid(as_uuid=False), nullable=False)
    category_uid = db.Column(db.Uuid(as_uuid=False), nullable=False)
    feed_item_uid = db.Column(db.Uuid(as_uuid=False), nullable=False)
    cash_minor = db.Column(db.BigInteger, nullable=False)
    tax_minor = db.Column(db.BigInteger, nullable=False, server_default='0')
    ni_minor = db.Column(db.BigInteger, nullable=False, server_default='0')
    payment_version = db.Column(db.Text, nullable=False)
    payment_label = db.Column(db.Text, nullable=False)
    active = db.Column(db.Boolean, nullable=False, server_default=db.true())
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    invoice = db.relationship('Invoice', back_populates='payments')
    transaction = db.relationship('Transaction', lazy='selectin')


class InvoiceSequence(BaseModel):
    __tablename__ = 'invoice_sequences'
    __table_args__ = (db.CheckConstraint('id = 1 AND last_number >= 0'), {'schema': 'toms'})
    id = db.Column(db.Integer, primary_key=True)
    last_number = db.Column(db.BigInteger, nullable=False)


class AccountReserve(BaseModel):
    """Owner earmarks within an account; this does not transfer bank money."""
    __tablename__ = 'account_reserves'
    __table_args__ = (
        db.CheckConstraint('tax_minor >= 0 AND ni_minor >= 0 AND revision > 0'),
        db.CheckConstraint('tax_minor::numeric + ni_minor::numeric <= 9223372036854775807'),
        {'schema': 'toms'},
    )
    account_uid = db.Column(db.Uuid(as_uuid=False), db.ForeignKey('toms.accounts.account_uid', ondelete='CASCADE'), primary_key=True)
    tax_minor = db.Column(db.BigInteger, nullable=False, server_default='0')
    ni_minor = db.Column(db.BigInteger, nullable=False, server_default='0')
    revision = db.Column(db.Integer, nullable=False, server_default='1')


class IncomeNoticeRead(BaseModel):
    """Acknowledgement of one version of a linked income receipt."""
    __tablename__ = 'income_notice_reads'
    __table_args__ = (
        db.ForeignKeyConstraint(['account_uid', 'category_uid', 'feed_item_uid'],
            ['toms.transactions.account_uid', 'toms.transactions.category_uid', 'toms.transactions.feed_item_uid'], ondelete='CASCADE'),
        db.CheckConstraint('char_length(payment_version) = 64'),
        {'schema': 'toms'},
    )
    account_uid = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    category_uid = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    feed_item_uid = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    payment_version = db.Column(db.Text, nullable=False)
