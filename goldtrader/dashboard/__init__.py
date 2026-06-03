"""Localhost monitoring dashboard for the gold supervisor.

A SEPARATE process from the supervisor. It only READS the on-disk artifacts the
supervisor produces (state.json, heartbeat.json, bias.json, journal.sqlite,
reflections/, goldtrader.jsonl) plus a short-lived MT5 read, and exposes a small
set of control actions (kill-switch, bias refresh, reflection, run-once, process
stop/restart). It never imports or mutates the supervisor's in-process state.
"""
