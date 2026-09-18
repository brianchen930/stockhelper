"""Minimal lifecycle rows and immutable prediction references in the existing DB."""
import json


FIELDS = ('stable_zone_id', 'symbol', 'timeframe', 'zone_low', 'zone_high', 'anchor_low', 'anchor_high',
          'previous_role', 'current_role', 'status', 'break_confirmed_at', 'flip_confirmed_at',
          'last_seen_at', 'last_observation_at', 'last_price', 'methods', 'origin_id', 'strength_score', 'strength_label', 'interaction')


def create_tables(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS zone_lifecycle (
        stable_zone_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
        zone_low REAL NOT NULL, zone_high REAL NOT NULL, anchor_low REAL NOT NULL, anchor_high REAL NOT NULL,
        previous_role TEXT, current_role TEXT NOT NULL, status TEXT NOT NULL,
        break_confirmed_at TEXT, flip_confirmed_at TEXT, last_seen_at TEXT NOT NULL,
        last_observation_at TEXT, last_price REAL, methods TEXT NOT NULL, origin_id TEXT,
        strength_score REAL, strength_label TEXT)''')
    connection.execute('CREATE INDEX IF NOT EXISTS zone_lifecycle_symbol ON zone_lifecycle(symbol,timeframe)')
    columns = {row[1] for row in connection.execute('PRAGMA table_info(zone_lifecycle)')}
    if 'interaction' not in columns:
        connection.execute('ALTER TABLE zone_lifecycle ADD COLUMN interaction TEXT')
    connection.execute('''CREATE TABLE IF NOT EXISTS zone_bayesian_events (
        prediction_id TEXT PRIMARY KEY, stable_zone_id TEXT NOT NULL,
        prediction_time TEXT NOT NULL, observation_time TEXT NOT NULL,
        support_probability REAL, predicted_level TEXT, model_reference TEXT,
        actual_outcome TEXT CHECK(actual_outcome IS NULL OR actual_outcome='BREAK'),
        outcome_time TEXT, UNIQUE(stable_zone_id,observation_time,model_reference))''')


class LifecycleStore:
    def __init__(self, connection_factory=None):
        if connection_factory is None:
            from app.database import get_connection
            connection_factory = get_connection
        self.connect = connection_factory

    @staticmethod
    def load(connection, symbol, timeframe):
        cursor = connection.execute('SELECT ' + ','.join(FIELDS) + ' FROM zone_lifecycle WHERE symbol=? AND timeframe=?', (symbol, timeframe))
        rows = [dict(zip(FIELDS, row)) for row in cursor.fetchall()]
        for row in rows:
            row['methods'] = json.loads(row['methods'])
            row['interaction'] = json.loads(row['interaction']) if row['interaction'] else None
        return rows

    @staticmethod
    def save(connection, records):
        updates = ','.join(k + '=excluded.' + k for k in FIELDS if k != 'stable_zone_id')
        for r in records:
            values = [json.dumps(r.get(k), allow_nan=False) if k == 'interaction' else
                      json.dumps(r.get(k, [])) if k == 'methods' else r.get(k) for k in FIELDS]
            connection.execute('INSERT INTO zone_lifecycle (' + ','.join(FIELDS) + ') VALUES (' + ','.join('?' for _ in FIELDS) + ') ON CONFLICT(stable_zone_id) DO UPDATE SET ' + updates, values)

    @staticmethod
    def resolve_predictions(connection, records):
        for r in records:
            if r['break_confirmed_at'] and r['previous_role'] == 'SUPPORT':
                # Only a prediction actually recorded BEFORE this break can mature.
                connection.execute('''UPDATE zone_bayesian_events SET actual_outcome='BREAK', outcome_time=?
                    WHERE stable_zone_id=? AND actual_outcome IS NULL AND prediction_time<? AND observation_time<?''',
                    (r['break_confirmed_at'], r['stable_zone_id'], r['break_confirmed_at'], r['break_confirmed_at']))

    @staticmethod
    def events(connection, symbol, timeframe):
        cursor = connection.execute('''SELECT e.* FROM zone_bayesian_events e
            JOIN zone_lifecycle z ON z.stable_zone_id=e.stable_zone_id WHERE z.symbol=? AND z.timeframe=?
            ORDER BY prediction_time,prediction_id''', (symbol, timeframe))
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
