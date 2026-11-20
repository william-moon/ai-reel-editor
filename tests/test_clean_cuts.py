import numpy as np
from reel_editor import audio as A

THR = -40.0


def db_from(spans, n=300):
    """10 ms frames: -70 dB silence, -20 dB where a (start, end) seconds span is voiced"""
    db = np.full(n, -70.0)
    for a, b in spans:
        db[int(a / A.HOP):int(b / A.HOP)] = -20.0
    return db


def test_onset_skips_previous_word_tail():
    # cut word 0.50-0.90 (whisper says it ends at 0.80, its tail really runs to 0.90), kept word starts 1.00
    db = db_from([(0.50, 0.90), (1.00, 1.50)])
    on, quiet_from = A.clean_onset(db, THR, s=0.95, lo=0.70)     # whisper start 50 ms early
    assert abs(on - 1.00) < 0.011
    assert quiet_from >= 0.89                                     # the cut can never reach back into the tail


def test_offset_stops_before_next_word():
    db = db_from([(1.00, 1.50), (1.62, 2.00)])
    off, quiet_to = A.clean_offset(db, THR, e=1.58, hi=1.80)      # whisper end 80 ms late
    assert abs(off - 1.50) < 0.011
    assert quiet_to <= 1.621


def test_run_together_cuts_at_quietest_frame():
    db = db_from([(1.00, 1.50), (1.50, 2.00)])
    db[int(1.49 / A.HOP)] = -30.0                                 # a small dip, still above the threshold
    on, qf = A.clean_onset(db, THR, s=1.50, lo=1.25)
    assert on == qf and abs(on - 1.49) < 0.011


def test_edge_db_flags_leaks():
    db = db_from([(1.00, 1.50)])
    h, t = A.edge_db(db, 0.98, 1.60)
    assert h > THR and t < THR
