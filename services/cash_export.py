"""Private snapshot export of explicit planning records, never authentication."""
import csv
import io
from tempfile import TemporaryFile
from zipfile import ZipFile, ZIP_DEFLATED

from models import Account, CashBalance, CashCommitment, CashPayment
from services.database.connection import db
from services.database.session import test_data_active
from services.tax.export import safe_csv_row

TABLES = {
    'commitments': (CashCommitment, 'id kind name currency amount_minor due_date recurrence end_date tax_year tax_component notes active revision'),
    'payments': (CashPayment, 'id commitment_id occurrence_date amount_minor tax_credit_minor paid_on reference active recorded_at changed_at'),
    'balance_observations': (CashBalance, 'account_uid amount_minor recorded_currency observed_at recorded_at revision'),
}


def package(session):
    output = TemporaryFile(mode='w+b')
    try:
        with ZipFile(output,'w',compression=ZIP_DEFLATED) as archive:
            archive.writestr('README.txt',
                ('SAMPLE DATA ONLY\n' if test_data_active() else 'Saved owner planning records\n') +
                'Money is in integer minor units. Keep currencies separate. Blank amounts are unknown.\n'
                'Commitments and payment history are manual records, not tax returns, bank instructions or extra income.\n'
                'HMRC charges start from the outstanding statement balance after existing credits; record only subsequent payments.\n'
                'tax_credit_minor is the explicitly attributed supported current-year portion of an active payment; it changes cash provision only, not tax owed.\n'
                'Cancelled schedules and voided payments remain in history. Do not subtract voided payments.\n'
                'CSV formula-like text is escaped. This archive contains private financial data.\n')
            for name,(model,fields) in TABLES.items():
                columns = fields.split()
                if model is CashBalance:
                    columns += ['currency','account_name']
                with archive.open(name+'.csv','w') as destination:
                    destination.write(b'\xef\xbb\xbf')
                    text = io.StringIO(newline='')
                    writer = csv.DictWriter(text,fieldnames=columns)
                    writer.writeheader()
                    destination.write(text.getvalue().encode())
                    query = db.select(model).order_by(*model.__table__.primary_key.columns)
                    records = session.scalars(query.execution_options(yield_per=500)) if model is not CashBalance else session.execute(
                        db.select(CashBalance,Account.currency,Account.name).join(Account).order_by(CashBalance.account_uid).execution_options(yield_per=500))
                    for record in records:
                        row = record[0] if model is CashBalance else record
                        values = {field:getattr(row,field) for field in fields.split()}
                        if model is CashBalance:
                            values.update(currency=record[1],account_name=record[2])
                        text.seek(0)
                        text.truncate()
                        writer.writerow(safe_csv_row(values))
                        destination.write(text.getvalue().encode('utf-8'))
        output.seek(0)
        return output
    except BaseException:
        output.close()
        raise
