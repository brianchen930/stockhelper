"""Incremental, point-in-time touch episodes for previously observed price zones.

No retrospective scan using a zone discovered in the future. Zone identities
are approximate and anchored at their first observation; never drift-match.
"""
from collections import deque
from dataclasses import dataclass, field
from statistics import mean, median

from .support_event import finite, valid_bar, is_support_touch, advance_exit_count, zones_match
from .volume_analysis import volume_at


def touch_rebound_trend(previous, recent, ratio=0.7):
    previous, recent = finite(previous), finite(recent)
    # Ratios between zero/negative rebounds do not measure rebound degradation.
    if previous is None or recent is None or previous <= 0:
        return 'insufficient_data'
    if recent < previous * ratio:
        return 'weakening'
    if recent > previous / ratio:
        return 'strengthening'
    return 'stable'


def evaluate_touch_reaction(future, reference, atr, config, same_bar_policy='failure'):
    """Touch label only: first High >= +1 ATR versus first Low <= -0.5 ATR.

    Both distances use the episode's first Close (not support_low). Requiring
    a full window even after an early hit keeps maturity unambiguous.
    """
    reference, atr = finite(reference), finite(atr)
    if (reference is None or atr is None or atr <= 0 or reference <= 0
            or len(future) != config.reaction_lookahead
            or not all(valid_bar(r.Low, r.High, r.Close) for r in future.itertuples())):
        return None
    success = failure = None
    for offset, row in enumerate(future.itertuples(), 1):
        if success is None and (row.High - reference) / atr >= config.touch_success_atr:
            success = offset
        if failure is None and (row.Low - reference) / atr <= -config.touch_failure_atr:
            failure = offset
    if success is not None and success == failure:
        label = same_bar_policy
    elif success is not None and (failure is None or success < failure):
        label = 'success'
    elif failure is not None:
        label = 'failure'
    else:
        label = 'neutral'
    rebound = finite((float(future.High.max()) - reference) / atr)
    breakdown = finite((float(future.Low.min()) - reference) / atr)
    if rebound is None or breakdown is None:
        return None
    return dict(touch_max_rebound_atr=rebound, touch_max_breakdown_atr=breakdown,
                touch_label=label, success_offset=success, failure_offset=failure)


@dataclass
class TouchEpisode:
    index: int
    date: str
    low: float
    high: float
    atr: float
    close: float
    bar_low: float
    bar_high: float
    volume: dict
    reaction: dict | None = None
    reaction_end_index: int | None = None
    exit_count: int = 0

    def snapshot(self, cutoff):
        matured = self.reaction_end_index is not None and self.reaction_end_index < cutoff
        return dict(touch_index=self.index, touch_date=self.date, support_low=self.low,
                    support_high=self.high, atr=self.atr, touch_close=self.close,
                    touch_low=self.bar_low, touch_high=self.bar_high, **self.volume,
                    reaction_end_index=self.reaction_end_index if matured else None,
                    reaction=self.reaction if matured else None)


@dataclass
class TouchTrack:
    low: float
    high: float
    atr: float
    first_seen: int
    latest_low: float
    latest_high: float
    episodes: list = field(default_factory=list)
    active: TouchEpisode | None = None


class TouchTracker:
    def __init__(self, data, atr_values, volume_data, config):
        self.data, self.atrs, self.volumes, self.config = data, atr_values, volume_data, config
        self.tracks = []
        self.pending = deque()
        self.last_index = -1

    def observe(self, index, zones):
        """Process each bar once, including bars with no newly detected supports."""
        if index <= self.last_index or self.last_index >= 0 and index != self.last_index + 1:
            raise ValueError('TouchTracker must advance monotonically, one bar at a time')
        self.last_index = index
        evidence = self.config.evidence
        while self.pending and self.pending[0].index + evidence.reaction_lookahead <= index:
            episode = self.pending.popleft()
            end = episode.index + evidence.reaction_lookahead
            episode.reaction = evaluate_touch_reaction(
                self.data.iloc[episode.index + 1:end + 1], episode.close, episode.atr,
                evidence, self.config.same_bar_policy)
            episode.reaction_end_index = end
        mapping = {}
        atr = finite(self.atrs[index])
        identity_atr = finite(self.atrs[index - 1]) if index else None
        identity_atr = identity_atr if identity_atr is not None and identity_atr > 0 else atr
        for zone in zones:
            low, high = finite(zone.get('low')), finite(zone.get('high'))
            if low is None or high is None or not 0 < low <= high or identity_atr is None or identity_atr <= 0:
                continue
            matches = [t for t in self.tracks if zones_match(low, high, t.low, t.high, t.atr, self.config.zone_match_atr)]
            track = min(matches, key=lambda t: abs(low + high - t.low - t.high), default=None)
            if track is None:
                track = TouchTrack(low, high, identity_atr, max(0, index - 1), low, high)
                self.tracks.append(track)
            track.latest_low, track.latest_high = low, high
            mapping[(low, high)] = track
        row = self.data.iloc[index]
        valid = valid_bar(row.Low, row.High, row.Close)
        for track in self.tracks:
            if track.active is not None:
                episode = track.active
                episode.exit_count = advance_exit_count(
                    row.Close if valid else None, episode.low, episode.high, episode.atr,
                    evidence.touch_exit_atr, episode.exit_count, side='either')
                if episode.exit_count >= evidence.touch_exit_bars:
                    track.active = None
                continue  # Never create an episode on the second exit bar itself.
            if valid and is_support_touch(row.Low, row.High, track.latest_low, track.latest_high,
                                          atr, self.config.touch_atr_threshold):
                episode = TouchEpisode(index, self.data.index[index].isoformat(),
                                       track.latest_low, track.latest_high, atr, float(row.Close),
                                       float(row.Low), float(row.High), volume_at(self.volumes, index, evidence))
                track.episodes.append(episode)
                track.active = episode
                self.pending.append(episode)
        return mapping

    def features(self, track, index):
        # The ongoing episode is today's touch, even if it started on a prior
        # bar while event cooldown was still active. Never count it as history.
        historical = [e for e in track.episodes if e.index < index and e is not track.active]
        mature = [e for e in historical if e.reaction is not None and e.reaction_end_index < index]
        rebounds = [e.reaction['touch_max_rebound_atr'] for e in mature]
        breakdowns = [e.reaction['touch_max_breakdown_atr'] for e in mature]
        pair = historical[-2:]
        trend = 'insufficient_data'
        if len(pair) == 2 and all(e.reaction is not None and e.reaction_end_index < index for e in pair):
            trend = touch_rebound_trend(pair[0].reaction['touch_max_rebound_atr'],
                                        pair[1].reaction['touch_max_rebound_atr'],
                                        self.config.evidence.weakening_ratio)
        return dict(historical_touch_count=len(historical), current_touch_number=len(historical) + 1,
                    support_touch_count=len(historical),
                    last_touch_date=historical[-1].date if historical else None,
                    bars_since_last_touch=index - historical[-1].index if historical else None,
                    first_touch_date=historical[0].date if historical else None,
                    support_age_bars=index - track.first_seen,
                    support_first_seen_date=self.data.index[track.first_seen].isoformat(),
                    current_touch_start_date=track.active.date if track.active else self.data.index[index].isoformat(),
                    historical_touch_reaction_count=len(mature),
                    historical_touch_avg_rebound_atr=mean(rebounds) if rebounds else None,
                    historical_touch_median_rebound_atr=median(rebounds) if rebounds else None,
                    historical_touch_max_rebound_atr=max(rebounds) if rebounds else None,
                    historical_touch_avg_breakdown_atr=mean(breakdowns) if breakdowns else None,
                    historical_touch_success_count=sum(e.reaction['touch_label'] == 'success' for e in mature) if mature else None,
                    historical_touch_failure_count=sum(e.reaction['touch_label'] == 'failure' for e in mature) if mature else None,
                    touch_rebound_weakening=None if trend == 'insufficient_data' else trend == 'weakening',
                    touch_rebound_trend=trend,
                    historical_touch_episodes=[e.snapshot(index) for e in historical])
