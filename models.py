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
