"""Owner-only account onboarding and CSV preview forms."""
from uuid import uuid4
from datetime import datetime, timezone
import csv
import io
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import BadRequest, Conflict
from models import Account, StatementImport, StatementMapping, StatementChange
from services import statement_accounts as accounts, statement_imports as imports, statement_review as review
from services.database.connection import db
from services.error_logging import log_failure
from services.web.sessions import require_dashboard_login
from services.web.test_data import activate_test_data
from services.web.activity import record_action
from services.web.presentation import format_amount
from services.invoices.workflow import LOCAL_TIME, lock_writes
from services.validation import uid
from routes.helpers import query_values

statement_accounts=Blueprint('statement_accounts',__name__,url_prefix='/dashboard/accounts')
statement_accounts.before_request(require_dashboard_login)


@statement_accounts.before_request
def owner():
    if request.authorization is not None or not current_user.is_authenticated:
        return redirect(url_for('login.sign_in'))


statement_accounts.before_request(activate_test_data)


def check(fields):
    query_values(set())
    if set(request.form)-fields-{'csrf_token'} or any(len(request.form.getlist(key))!=1 for key in request.form):
        raise BadRequest('Supply each account field once.')


def page(mode,**values):
    return render_template('statement_accounts.html',mode=mode,current_user=current_user,
        values=values.pop('form_values',request.form),format_amount=format_amount,local_time=LOCAL_TIME,
        history_date=lambda value:datetime.fromisoformat(value).astimezone(LOCAL_TIME).strftime('%d %b %Y'),**values)


def recent_changes(identity, *, transfers_only=False):
    query=db.select(StatementChange).where(db.or_(StatementChange.account_uid==identity,
        StatementChange.after.contains([{'account_uid':identity}])))
    if transfers_only:
        query=query.where(StatementChange.kind=='transfer')
    return db.session.scalars(query.order_by(StatementChange.created_at.desc()).limit(20)).all()


@statement_accounts.errorhandler(SQLAlchemyError)
def unavailable(error):
    db.session.rollback()
    log_failure('statement-accounts.database',error)
    return page('unavailable',error='Accounts are unavailable. Check PostgreSQL and migrations.'),503


@statement_accounts.get('')
def index():
    query_values(set())
    rows=db.session.scalars(db.select(Account).order_by(Account.name,Account.account_uid)).all()
    return page('index',accounts=rows,coverage={row.account_uid:accounts.coverage_view(row,db.session) for row in rows if row.provider=='statement'})


@statement_accounts.route('/new',methods=['GET','POST'])
def new():
    query_values(set())
    error,status=None,200
    if request.method=='POST':
        try:
            check({'identity','category','name','bank_name','currency','opened_on'})
            row=accounts.save(request.form.get('identity',''),request.form)
            record_action('account.create',row.account_uid)
            db.session.commit()
            return redirect(url_for('.detail',identity=row.account_uid),code=303)
        except (ValueError,BadRequest,Conflict) as failure:
            db.session.rollback()
            error=failure.description if isinstance(failure,(BadRequest,Conflict)) else str(failure)
            status=409 if isinstance(failure,Conflict) else 400
    return page('new',error=error,identity=request.form.get('identity') or str(uuid4()),category=request.form.get('category') or str(uuid4())),status


@statement_accounts.route('/<uuid:identity>',methods=['GET','POST'])
def detail(identity):
    query_values(set())
    row=accounts.account(identity)
    error,status=None,200
    if request.method=='POST':
        try:
            action=request.form.get('action')
            allowed={'action','version','name','bank_name','currency','opened_on'} if action=='edit' else {'action','version','from_on','through_on','history_version','confirm'} if action=='history' else {'action','version','closed_on','confirm'} if action=='close' else {'action','version','confirm'}
            check(allowed)
            row=accounts.account(identity,request.form.get('version',''))
            if row.provider!='statement':
                raise ValueError('Starling manages this account.')
            if action=='edit':
                accounts.save(str(identity),request.form,request.form.get('version',''))
            elif action=='history':
                accounts.confirm_history(row,request.form)
            elif action=='close':
                accounts.close(row,request.form)
            elif action=='reopen' and row.closed_on and request.form.get('confirm')=='1':
                row.closed_on,row.cash_active=None,True
                row.revision+=1
                accounts.invalidate_history(row.account_uid)
            elif action=='formats' and request.form.get('confirm')=='1':
                db.session.execute(db.delete(StatementMapping).where(StatementMapping.account_uid==row.account_uid))
            else:
                raise BadRequest('Choose an account action.')
            record_action('account.'+action,row.account_uid)
            db.session.commit()
            return redirect(url_for('.detail',identity=identity),code=303)
        except (ValueError,BadRequest,Conflict) as failure:
            db.session.rollback()
            row=accounts.account(identity)
            error=failure.description if isinstance(failure,(BadRequest,Conflict)) else str(failure)
            status=409 if isinstance(failure,Conflict) else 400
    batches=db.session.scalars(db.select(StatementImport).where(StatementImport.account_uid==row.account_uid,
        StatementImport.status=='imported').order_by(StatementImport.imported_at.desc()).limit(20)).all()
    previews=db.session.scalars(db.select(StatementImport).where(StatementImport.account_uid==row.account_uid,
        StatementImport.status.in_(('mapping','preview')),StatementImport.expires_at>datetime.now(timezone.utc))
        .order_by(StatementImport.created_at.desc()).limit(20)).all()
    return page('detail',row=row,coverage=accounts.coverage_view(row,db.session),batches=batches,previews=previews,error=error),status


@statement_accounts.route('/<uuid:identity>/import',methods=['GET','POST'])
def upload(identity):
    query_values(set())
    row=accounts.account(identity)
    if row.provider!='statement':
        raise BadRequest('Add a statement account before uploading a CSV.')
    error,status=None,200
    if request.method=='POST':
        try:
            check({'encoding','delimiter','version','use_saved'})
            if set(request.files)!={'statement'} or len(request.files.getlist('statement'))!=1:
                raise ValueError('Choose one CSV statement.')
            row=accounts.account(identity,request.form.get('version',''))
            saved=imports.upload(row,request.files['statement'].stream.read(imports.MAX_BYTES+1),
                request.form.get('encoding',''),request.form.get('delimiter',''))
            db.session.flush()
            if request.form.get('use_saved')=='1':
                saved,_=imports.reuse_mapping(saved)
            db.session.commit()
            return redirect(url_for('.mapping' if saved.status=='mapping' else '.preview',identity=saved.id),code=303)
        except (ValueError,BadRequest,Conflict) as failure:
            db.session.rollback()
            error=failure.description if isinstance(failure,(BadRequest,Conflict)) else str(failure)
            status=409 if isinstance(failure,Conflict) else 400
    return page('upload',row=row,error=error),status


@statement_accounts.route('/<uuid:identity>/entry',methods=['GET','POST'])
def entry(identity):
    query_values(set())
    row=accounts.account(identity)
    if row.provider!='statement':
        raise BadRequest('Choose a statement account.')
    error,status=None,200
    if request.method=='POST':
        try:
            check({'entry_id','version','date','amount','direction','payer','reference'})
            intent=uid(request.form.get('entry_id',''))
            row=accounts.account(identity,request.form.get('version',''))
            amount=imports.money(request.form.get('amount',''),row.currency,'point')
            if amount is None or amount<0 or request.form.get('direction') not in ('IN','OUT'):
                raise ValueError('Enter a non-negative amount and choose money in or out.')
            from services.transactions.income_form import display_amount
            data=io.StringIO(newline='')
            writer=csv.writer(data)
            writer.writerow(['Date','Credit','Debit','Payment','Reference','Entry intent'])
            writer.writerow([request.form.get('date',''),display_amount(amount,row.currency) if request.form['direction']=='IN' else '',
                display_amount(amount,row.currency) if request.form['direction']=='OUT' else '',
                request.form.get('payer',''),request.form.get('reference',''),intent])
            saved=imports.upload(row,data.getvalue().encode())
            db.session.flush()
            saved=imports.map_rows(saved,dict(date='0',credit='1',debit='2',payer='3',reference='4',
                date_format='iso',amount_mode='separate',decimal='point'))
            db.session.commit()
            return redirect(url_for('.preview',identity=saved.id),code=303)
        except (ValueError,BadRequest,Conflict) as failure:
            db.session.rollback()
            error=failure.description if isinstance(failure,(BadRequest,Conflict)) else str(failure)
            status=409 if isinstance(failure,Conflict) else 400
    return page('entry',row=row,error=error,entry_id=request.form.get('entry_id') or str(uuid4())),status


@statement_accounts.route('/imports/<uuid:identity>/map',methods=['GET','POST'])
def mapping(identity):
    query_values(set())
    saved=imports.batch(identity)
    if saved.status!='mapping':
        return redirect(url_for('.preview',identity=identity),code=303)
    error,status=None,200
    if request.method=='POST':
        try:
            check(imports.MAP_FIELDS|{'remember'})
            saved=imports.map_rows(saved,request.form)
            db.session.commit()
            return redirect(url_for('.preview',identity=saved.id),code=303)
        except (ValueError,BadRequest,Conflict) as failure:
            db.session.rollback()
            saved=imports.batch(identity)
            error=failure.description if isinstance(failure,(BadRequest,Conflict)) else str(failure)
            status=409 if isinstance(failure,Conflict) else 400
    return page('mapping',saved=saved,row=accounts.account(saved.account_uid),error=error,
        form_values=request.form if request.method=='POST' else imports.mapping_defaults(saved.headers)),status


@statement_accounts.route('/imports/<uuid:identity>/preview',methods=['GET','POST'])
def preview(identity):
    query_values(set())
    saved=imports.batch(identity)
    if saved.status=='mapping':
        return redirect(url_for('.mapping',identity=identity),code=303)
    if saved.status=='discarded':
        raise BadRequest('This upload was discarded. Upload a file again.')
    error,status=None,200
    if request.method=='POST':
        try:
            check({'confirm','fingerprint'}|{f"row_{item['row_number']}" for item in saved.rows})
            saved=imports.confirm(saved,request.form)
            record_action('statement.import',saved.id)
            db.session.commit()
            return redirect(url_for('.preview',identity=saved.id),code=303)
        except (ValueError,BadRequest,Conflict) as failure:
            db.session.rollback()
            saved=imports.batch(identity)
            error=failure.description if isinstance(failure,(BadRequest,Conflict)) else str(failure)
            status=409 if isinstance(failure,Conflict) else 400
    rows,fingerprint=imports.preview(saved) if saved.status=='preview' else ([],None)
    return page('preview',saved=saved,row=accounts.account(saved.account_uid),rows=rows,fingerprint=fingerprint,error=error),status


@statement_accounts.route('/<uuid:identity>/transfers',methods=['GET','POST'])
def transfers(identity):
    query_values(set())
    row=accounts.account(identity)
    error,status=None,200
    if request.method=='POST':
        try:
            check({'left','right','left_version','right_version','confirm'})
            review.match(row.account_uid,request.form)
            record_action('statement.transfer',row.account_uid)
            db.session.commit()
            flash('Transfer confirmed. Both payments are reviewed as an own-account transfer.','statement')
            return redirect(url_for('.transfers',identity=identity),code=303)
        except (ValueError,BadRequest,Conflict) as failure:
            db.session.rollback()
            error=failure.description if isinstance(failure,(BadRequest,Conflict)) else str(failure)
            status=409 if isinstance(failure,Conflict) else 400
    pairs,truncated=review.suggestions(row.account_uid)
    return page('transfers',row=row,pairs=pairs,truncated=truncated,
        changes=recent_changes(row.account_uid,transfers_only=True),error=error),status


@statement_accounts.route('/imports/<uuid:identity>/corrections',methods=['GET','POST'])
def corrections(identity):
    query=query_values({'page'})
    number=query.get('page','1')
    if not number.isdigit() or not 1<=int(number)<=20:
        raise BadRequest('Choose a correction page from 1 through 20.')
    number=int(number)
    saved=imports.batch(identity)
    if saved.status!='imported':
        raise BadRequest('Import the statement before correcting its saved records.')
    error,status=None,200
    if request.method=='POST':
        try:
            # The validated page is a read/navigation context, never a write scope.
            bulk=request.form.get('action')=='bulk-exclude'
            allowed={'csrf_token','action','selected','selection_version','reason','confirm'} if bulk else {'csrf_token','transaction','version','action','date','amount','direction','payer','reference','reason','confirm'}
            if set(request.form)-allowed or any(len(request.form.getlist(key))!=1 for key in request.form if key!='selected'):
                raise BadRequest('Supply each correction field once.')
            if bulk:
                review.bulk_exclude(saved,request.form,request.form.getlist('selected'),number)
            else:
                review.correct(saved,request.form)
            record_action('statement.correct',saved.id)
            db.session.commit()
            flash('Correction saved with its history. Changed or restored settled payments need review again.','statement')
            return redirect(url_for('.corrections',identity=identity,page=number),code=303)
        except (ValueError,BadRequest,Conflict) as failure:
            db.session.rollback()
            error=failure.description if isinstance(failure,(BadRequest,Conflict)) else str(failure)
            status=409 if isinstance(failure,Conflict) else 400
    changes=recent_changes(saved.account_uid)
    from services.transactions.income_form import display_amount
    records=review.imported_rows(saved.id,number)
    return page('corrections',saved=saved,row=accounts.account(saved.account_uid),
        records=records,selection_version=review.selection_version(records),changes=changes,display_amount=display_amount,
        page_number=number,page_count=(saved.row_count+49)//50,error=error),status


@statement_accounts.post('/imports/<uuid:identity>/discard')
def discard(identity):
    check(set())
    lock_writes()
    saved=imports.batch(identity)
    if saved.status=='imported':
        raise Conflict('Imported evidence cannot be discarded.')
    saved.status,saved.headers,saved.rows='discarded',[],[]
    db.session.commit()
    return redirect(url_for('.detail',identity=saved.account_uid),code=303)
