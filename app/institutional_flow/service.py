"""Target-date refresh with bounded retries. Explicit as_of never downloads data."""
from datetime import datetime, timedelta
import logging
from .config import DEFAULT_CONFIG
from .features import TAIPEI, build_context, taipei_time, unknown
from .freshness import metadata, session_dates
from .provider import OfficialFirstProvider, detect_market
from .storage import FlowStore

logger = logging.getLogger(__name__)

class InstitutionalFlowService:
    def __init__(self, store=None, provider=None, config=DEFAULT_CONFIG, clock=None):
        self.store = store or FlowStore()
        self.provider = provider or OfficialFirstProvider(config)
        self.config = config
        self.clock = clock or (lambda: datetime.now(TAIPEI))

    def context(self, symbol, *, as_of=None, replay=False, strict=True):
        now = taipei_time(self.clock())
        cutoff = taipei_time(as_of) if as_of is not None else now
        code = symbol.split('.')[0]
        try:
            market = detect_market(symbol)
            target, _ = session_dates(cutoff, self.config)
            rows = self.store.history(code, cutoff, strict=strict, market=market)
            latest = rows[-1]['date'] if rows else None
            refreshed = False
            if as_of is None and not replay and latest != target and self.store.refresh_due(
                    code, market, target, now, self.config.retry_seconds):
                refreshed = True
                self.store.mark_refresh(code, market, target, now)
                start = (now.date() - timedelta(days=self.config.history_days)).isoformat()
                try:
                    fetched = self.provider.fetch(symbol, start, target)
                    observed = taipei_time(self.clock())
                    accepted = []
                    for row in fetched:
                        if row['symbol'] != code or row['market'] != market or not start <= row['date'] <= target:
                            continue
                        row = dict(row)
                        row['available_at'] = min(taipei_time(row['available_at']), observed).isoformat()
                        accepted.append(row)
                    self.store.upsert(accepted, observed.isoformat())
                    cutoff = observed
                except Exception as error:
                    logger.warning('[Institutional] %s source=%s target=%s fetch_failed=%s',
                                   code, market, target, type(error).__name__)
                rows = self.store.history(code, cutoff, strict=strict, market=market)
            context = build_context(rows, cutoff, self.config)
            if refreshed:
                log = logger.info if context['freshness'] == 'FRESH' else logger.warning
                log('[Institutional] %s source=%s target=%s actual=%s freshness=%s',
                    code, context.get('source', market), target, context['data_date'], context['freshness'])
            context['availability_policy'] = 'observed_point_in_time' if strict else 'next_day_assumption_revised_history'
            return context
        except Exception as error:
            logger.warning('[Institutional] %s unavailable=%s', code, type(error).__name__)
            return {**unknown('暫無可用資料；本次交易建議暫不採用法人籌碼訊號'),
                    **metadata(None, cutoff, self.config)}
