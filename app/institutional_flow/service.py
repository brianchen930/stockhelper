"""Daily refresh and fail-open context retrieval. Replay never downloads data."""
from datetime import datetime, timedelta
from .config import DEFAULT_CONFIG
from .features import TAIPEI, build_context, taipei_time, unknown
from .provider import FinMindProvider
from .storage import FlowStore


class InstitutionalFlowService:
    def __init__(self, store=None, provider=None, config=DEFAULT_CONFIG):
        self.store = store or FlowStore()
        self.provider = provider or FinMindProvider(config)
        self.config = config

    def context(self, symbol, *, as_of=None, replay=False, strict=True):
        now = datetime.now(TAIPEI)
        cutoff = taipei_time(as_of) if as_of is not None else now
        code = symbol.split('.')[0]
        try:
            # Explicit historical requests are read-only, even without replay=True.
            if not replay and cutoff.date() == now.date():
                day = now.date().isoformat()
                if not self.store.attempted(code, day):
                    self.store.mark_attempt(code, day)
                    end = (now.date() - timedelta(days=1)).isoformat()
                    start = (now.date() - timedelta(days=self.config.history_days)).isoformat()
                    try:
                        rows = self.provider.fetch(symbol, start, end)
                        observed = datetime.now(TAIPEI)
                        self.store.upsert(rows, observed.isoformat())
                        if as_of is None:
                            cutoff = observed
                    except Exception:
                        pass  # A valid existing daily cache remains useful.
            rows = self.store.history(code, cutoff, strict=strict)
            context = build_context(rows, cutoff, self.config)
            context['availability_policy'] = 'observed_point_in_time' if strict else 'next_day_assumption_revised_history'
            return context
        except Exception:
            return unknown()
