"""Flask-SQLAlchemy models mapped to the existing toms tables."""

from sqlalchemy.dialects.postgresql import JSONB

from services.database.connection import db


class BaseModel(db.Model):
    __abstract__ = True
    __table_args__ = {"schema": "toms"}

    def as_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}


class Account(BaseModel):
    __tablename__ = "accounts"

    account_uid = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    default_category_uid = db.Column(db.Uuid(as_uuid=False), nullable=False)
    currency = db.Column(db.Text, nullable=False)
    name = db.Column(db.Text)
    account_type = db.Column(db.Text)
    opened_at = db.Column(db.DateTime(timezone=True))
    raw_payload = db.Column(JSONB, nullable=False)
    fetched_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())


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


class OwnerSetup(BaseModel):
    __tablename__ = "owner_setup"
    id = db.Column(db.Integer, primary_key=True)
    token_hash = db.Column(db.Text, nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)


class ExpenseDeduction(BaseModel):
    """One eligible portion of a bank expense, claimed once against one stream."""
    __tablename__ = 'expense_deductions'
    __table_args__ = (
        db.ForeignKeyConstraint(['shift_id', 'income_stream_id'], ['toms.income_shifts.id', 'toms.income_shifts.income_stream_id']),
        db.ForeignKeyConstraint(['account_uid', 'category_uid', 'feed_item_uid'],
            ['toms.transactions.account_uid', 'toms.transactions.category_uid', 'toms.transactions.feed_item_uid'], ondelete='CASCADE'),
        db.CheckConstraint('amount_minor > 0 AND amount_minor <= recorded_amount_minor'),
        db.CheckConstraint("char_length(trim(purpose)) BETWEEN 1 AND 1000"),
        db.CheckConstraint("category IN ('general','vehicle_running','parking_tolls')"),
        db.CheckConstraint("char_length(vehicle_key) <= 40 AND (category <> 'vehicle_running' OR char_length(trim(vehicle_key)) > 0)"),
        {'schema': 'toms'},
    )
    account_uid = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    category_uid = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    feed_item_uid = db.Column(db.Uuid(as_uuid=False), primary_key=True)
    income_stream_id = db.Column(db.Uuid(as_uuid=False), db.ForeignKey('toms.income_streams.id'), nullable=False, index=True)
    shift_id = db.Column(db.Uuid(as_uuid=False), index=True)
    amount_minor = db.Column(db.BigInteger, nullable=False)
    purpose = db.Column(db.Text, nullable=False)
    category = db.Column(db.Text, nullable=False)
    vehicle_key = db.Column(db.Text, nullable=False, server_default='')
    recorded_amount_minor = db.Column(db.BigInteger, nullable=False)
    recorded_currency = db.Column(db.Text, nullable=False)
    recorded_time = db.Column(db.DateTime(timezone=True), nullable=False)
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
