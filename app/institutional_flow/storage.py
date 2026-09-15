"""Current daily rows plus immutable observed versions for point-in-time replay."""
from contextlib import closing
import json

RAW_COLUMNS = ('symbol', 'market', 'date', 'foreign_buy', 'foreign_sell', 'foreign_net',
    'investment_trust_buy', 'investment_trust_sell', 'investment_trust_net',
    'dealer_buy', 'dealer_sell', 'dealer_net', 'total_institutional_net', 'volume',
    'foreign_net_ratio', 'investment_trust_net_ratio', 'dealer_net_ratio',
    'total_institutional_net_ratio', 'source', 'available_at')


def create_tables(connection):
    text_columns = {'symbol', 'market', 'date', 'source', 'available_at'}
    columns = ','.join(name + (' TEXT' if name in text_columns else ' REAL') + ' NOT NULL' for name in RAW_COLUMNS)
    connection.execute(f'''CREATE TABLE IF NOT EXISTS institutional_flow (
        id INTEGER PRIMARY KEY, {columns}, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(symbol,date))''')
    connection.execute('''CREATE TABLE IF NOT EXISTS institutional_flow_versions (
        symbol TEXT NOT NULL, date TEXT NOT NULL, observed_at TEXT NOT NULL, payload TEXT NOT NULL,
        PRIMARY KEY(symbol,date,observed_at))''')
    connection.execute('''CREATE TABLE IF NOT EXISTS institutional_fetch_state (
        symbol TEXT PRIMARY KEY, attempted_date TEXT NOT NULL)''')
    connection.execute('''CREATE TABLE IF NOT EXISTS institutional_support_events (
        id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, timestamp TEXT NOT NULL,
        support_low REAL NOT NULL, support_high REAL NOT NULL,
        base_probability REAL, adjusted_probability REAL, institutional_score REAL,
        institutional_level TEXT NOT NULL, context_json TEXT NOT NULL,
        outcome TEXT CHECK(outcome IN ('HOLD','BREAK')), outcome_available_at TEXT,
        UNIQUE(symbol,timestamp,support_low,support_high))''')


class FlowStore:
    def __init__(self, connection_factory=None):
        if connection_factory is None:
            from app.database import get_connection
            connection_factory = get_connection
        self.connect = connection_factory
        with closing(self.connect()) as con, con:
            create_tables(con)

    def attempted(self, symbol, day):
        with closing(self.connect()) as con:
            row = con.execute('SELECT attempted_date FROM institutional_fetch_state WHERE symbol=?', (symbol,)).fetchone()
            return row is not None and row[0] == day

    def mark_attempt(self, symbol, day):
        with closing(self.connect()) as con, con:
            con.execute('INSERT INTO institutional_fetch_state VALUES (?,?) ON CONFLICT(symbol) DO UPDATE SET attempted_date=excluded.attempted_date', (symbol, day))

    def upsert(self, rows, observed_at):
        with closing(self.connect()) as con, con:
            for row in rows:
                payload = json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False)
                previous = con.execute('SELECT payload FROM institutional_flow_versions WHERE symbol=? AND date=? ORDER BY observed_at DESC LIMIT 1', (row['symbol'], row['date'])).fetchone()
                if previous is None or previous[0] != payload:
                    con.execute('INSERT INTO institutional_flow_versions VALUES (?,?,?,?)', (row['symbol'], row['date'], observed_at, payload))
                cols = RAW_COLUMNS + ('created_at', 'updated_at')
                updates = ','.join(f'{c}=excluded.{c}' for c in RAW_COLUMNS if c not in ('symbol', 'date')) + ',updated_at=excluded.updated_at'
                con.execute(f"INSERT INTO institutional_flow ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)}) ON CONFLICT(symbol,date) DO UPDATE SET {updates}",
                            [row[c] for c in RAW_COLUMNS] + [observed_at, observed_at])

    def history(self, symbol, as_of, *, strict=True):
        from .features import taipei_time
        cutoff = taipei_time(as_of)
        with closing(self.connect()) as con:
            versions = con.execute('SELECT date,observed_at,payload FROM institutional_flow_versions WHERE symbol=? AND date<? ORDER BY observed_at', (symbol.split('.')[0], cutoff.date().isoformat())).fetchall()
        selected = {}
        for day, observed, payload in versions:
            row = json.loads(payload)
            if taipei_time(row['available_at']) <= cutoff and (not strict or taipei_time(observed) <= cutoff):
                selected[day] = row
        return [selected[day] for day in sorted(selected)]

    def save_event(self, symbol, timestamp, candidate, context):
        with closing(self.connect()) as con, con:
            con.execute('''INSERT INTO institutional_support_events
                (symbol,timestamp,support_low,support_high,base_probability,adjusted_probability,
                 institutional_score,institutional_level,context_json) VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(symbol,timestamp,support_low,support_high) DO NOTHING''',
                (symbol, timestamp, candidate['support_low'], candidate['support_high'],
                 candidate['base_support_probability'], candidate['adjusted_support_probability'],
                 context['institutional_score'], context['institutional_level'],
                 json.dumps(context, ensure_ascii=False, allow_nan=False)))

    def resolve_event(self, event_id, outcome, available_at):
        from .features import taipei_time
        if outcome not in ('HOLD', 'BREAK'):
            raise ValueError('Outcome must be HOLD or BREAK')
        with closing(self.connect()) as con, con:
            row = con.execute('SELECT timestamp FROM institutional_support_events WHERE id=?', (event_id,)).fetchone()
            if row is None or taipei_time(available_at) <= taipei_time(row[0]):
                raise ValueError('Outcome must mature after prediction')
            con.execute('UPDATE institutional_support_events SET outcome=?,outcome_available_at=? WHERE id=?', (outcome, taipei_time(available_at).isoformat(), event_id))
