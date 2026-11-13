#!/usr/bin/env python3
"""fx_core: the canonical helpers of fx-trader-skill. Standard library only, Python 3.8.

Every skill script stays self-contained, so it COPIES the helpers it uses from this
file, verbatim: same name, same body, same docstring. Never edit a copied helper. If
you need other behaviour, write a new function with a new name in your script.
tests/test_repo.py checks that each copy is the same text as here. Helpers raise
ValueError with a plain message; a script wraps that in its own error class.

Times are naive datetimes. UTC inside; Karachi time (PKT) is UTC+5 all year (no
daylight saving) and is what people see. New York, London, Sydney and Tokyo times come
from hand-made daylight saving rules: no time zone files are used, and the machine's
own zone never matters.

Prices come from Yahoo Finance only: at least PACE_SECONDS between two requests, the
honest User-Agent UA, and the cache in fx/data/yahoo/. Never with --offline or --now.
The pack never places an order.
"""

# Imports used below (copy the ones your helpers need):
import copy
import datetime
import difflib
import gzip
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP


# ---------------------------------------------------------------- copy together
# Each line is a helper and the names from this file it uses. Copy those too, and
# what they use in turn. "-" means it uses nothing else from this file.
# This table is made by tests/core_src/assemble.py from the code itself.
# group files and config, words
#   ORDER_FIELDS: -
#   FRESH_CHECK_MINUTES: -
#   load_json: -
#   save_json_atomic: -
#   fx_paths: -
#   fingerprint: ORDER_FIELDS
#   load_doc: load_json
#   check_objects: -
#   check_symbol: -
#   check_side: -
#   check_style: -
#   check_utc: -
#   _positive_number: -
#   check_price: _positive_number
#   check_lots: _positive_number
#   check_close_order: -
#   JSON_VERSION: -
#   _words_dec: -
#   money: _words_dec
#   signed_money: _words_dec, money
#   price_text: _words_dec
#   lots_text: _words_dec
#   side_word: -
#   age_text: _words_dec
#   scale_note: -
#   broker_note: -
#   both_times: ny_label, pkt_label
#   add_common: -
#   fail: -
#   emit_json: -
# group instruments and rounding
#   _dec_clean: -
#   _price_dec: _dec_clean
#   instrument: _dec_clean
#   _to_tick: _dec_clean
#   round_price: _price_dec, _to_tick
#   round_away: _price_dec, _to_tick
#   to_points: _dec_clean
#   from_points: _dec_clean
#   pips_text: _dec_clean, to_points
#   spread_price: _dec_clean
#   ask_of: _price_dec, round_price, spread_price
# group clock, dst, fx day and sessions, market hours, calendar tables, economic calendar
#   KARACHI_HOURS: -
#   DAY_NAMES: -
#   MONTH_NAMES: -
#   utc_now: -
#   to_pkt: KARACHI_HOURS
#   pkt_to_utc: KARACHI_HOURS
#   parse_now: pkt_to_utc, utc_now
#   utc_iso: -
#   parse_utc: -
#   ts_to_utc: -
#   utc_to_ts: -
#   clock_label: -
#   day_label: DAY_NAMES
#   short_date: DAY_NAMES, MONTH_NAMES
#   pkt_label: DAY_NAMES, clock_label, short_date, to_pkt
#   ny_label: clock_label, short_date, to_ny
#   parse_hhmm: -
#   nth_weekday: -
#   last_weekday: -
#   us_dst: nth_weekday
#   uk_dst: last_weekday
#   sydney_dst: nth_weekday
#   to_ny: us_dst
#   ny_to_utc: us_dst
#   to_london: uk_dst
#   london_to_utc: uk_dst
#   to_sydney: sydney_dst
#   sydney_to_utc: sydney_dst
#   to_tokyo: -
#   tokyo_to_utc: -
#   city_to_utc: london_to_utc, ny_to_utc, sydney_to_utc, tokyo_to_utc
#   SERVER_TIME_BAD: -
#   server_offset: SERVER_TIME_BAD
#   server_time: server_offset, to_ny
#   server_to_utc: ny_to_utc, server_offset
#   odd_week: uk_dst, us_dst
#   DAY_ENDS: -
#   FULL_DAY_NAMES: -
#   SESSIONS: -
#   OR_WINDOWS: -
#   check_day_end: DAY_ENDS
#   parse_fx_date: FULL_DAY_NAMES
#   at_time: parse_hhmm
#   fx_day: check_day_end, to_ny
#   fx_day_bounds: check_day_end, ny_to_utc, parse_fx_date
#   full_fx_holiday: group_closed
#   next_fx_day: full_fx_holiday, parse_fx_date
#   fx_days_between: full_fx_holiday, parse_fx_date
#   add_fx_days: next_fx_day, parse_fx_date
#   last_fx_day: fx_day, to_ny
#   fx_week_bounds: fx_day, ny_to_utc, parse_fx_date, to_ny
#   pkt_time_on_fx_day: fx_day_bounds, parse_hhmm, pkt_to_utc, to_pkt
#   session_bounds: SESSIONS, at_time, city_to_utc, parse_fx_date
#   opening_range_window: OR_WINDOWS, at_time, city_to_utc, parse_fx_date
#   TABLES_CHECKED_ON: -
#   TABLES_END: -
#   TABLES_START: -
#   HOLIDAYS: -
#   EARLY_CLOSES: -
#   NFP_DATES: -
#   COT_RELEASES: -
#   CB_MEETINGS: -
#   EIA_TIMES: -
#   EIA_MOVES: -
#   ENERGY_KEYS: -
#   LBMA_TIMES: -
#   make_event: -
#   builtin_events: CB_MEETINGS, COT_RELEASES, EIA_MOVES, EIA_TIMES, HOLIDAYS, NFP_DATES, TABLES_END, at_time, city_to_utc, make_event, ny_to_utc, to_ny
#   key_times: HOLIDAYS, LBMA_TIMES, at_time, full_fx_holiday, london_to_utc, ny_to_utc, parse_fx_date
#   HOURS: -
#   BROKER_TEMPLATES: -
#   DATA_TEMPLATES: -
#   MARKET_WORDS: -
#   hours_template: HOURS
#   group_closed: HOLIDAYS, parse_fx_date
#   early_close_utc: EARLY_CLOSES, at_time, ny_to_utc, parse_fx_date
#   yahoo_no_data_cuts: HOLIDAYS, at_time, ny_to_utc, parse_fx_date
#   open_spans: DATA_TEMPLATES, at_time, city_to_utc, early_close_utc, group_closed, hours_template, ny_to_utc, parse_fx_date, yahoo_no_data_cuts
#   day_close_utc: open_spans
#   holiday_note: EARLY_CLOSES, HOLIDAYS, MARKET_WORDS, TABLES_END, TABLES_START, early_close_utc, hours_template, ny_label, parse_fx_date, pkt_label, short_date
#   market_state: DATA_TEMPLATES, early_close_utc, fx_day, group_closed, holiday_note, hours_template, open_spans, parse_fx_date, to_ny, yahoo_no_data_cuts
#   is_open: market_state
#   entry_cutoff: day_close_utc, pkt_time_on_fx_day
#   last_done_fx_day: fx_day_bounds, last_fx_day, open_spans, parse_fx_date
#   CALENDAR_MIN_REFRESH_S: -
#   CALENDAR_AHEAD_DAYS: -
#   CALENDAR_TIMEOUT: -
#   FEED_TITLES: -
#   calendar_week: to_ny
#   event_label: -
#   feed_kind: FEED_TITLES
#   parse_ff: CB_MEETINGS, at_time, feed_kind, make_event, ny_to_utc, parse_utc
#   event_symbols: ENERGY_KEYS
#   event_impact: ENERGY_KEYS
#   quiet_windows: event_impact, event_symbols
#   in_quiet_window: quiet_windows
#   next_events: event_impact
#   calendar_get: CALENDAR_TIMEOUT, UA
#   merge_builtin: CB_MEETINGS, FEED_TITLES
#   calendar_good_rows: -
#   calendar_refresh: calendar_get, calendar_good_rows, calendar_week, parse_utc, save_json_atomic, utc_iso
#   calendar_file_doc: load_json
#   load_calendar: CALENDAR_AHEAD_DAYS, CALENDAR_MIN_REFRESH_S, at_time, builtin_events, calendar_file_doc, calendar_refresh, calendar_week, event_symbols, merge_builtin, ny_to_utc, parse_ff, parse_utc
#   calendar_info: calendar_file_doc, calendar_week, parse_utc
# group yahoo client, cache, bars, rolls, broker prices
#   UA: -
#   YAHOO_HOSTS: -
#   PACE_SECONDS: -
#   TIMEOUT: -
#   SPARK_MAX: -
#   LOOKBACK: -
#   FIRST_RANGE: -
#   MIN_REFRESH_S: -
#   STEP_S: -
#   SLEEP: -
#   RETRY_WAIT_S: -
#   _last_request: -
#   _last_host: -
#   NoData: -
#   chart_url: -
#   _url_symbol: -
#   _url_interval: -
#   is_contract_symbol: -
#   _yahoo_error: NoData, is_contract_symbol
#   _error_description: -
#   _pace: PACE_SECONDS, SLEEP, _last_request
#   _gunzip: -
#   _one_get: UA, _gunzip
#   yahoo_get: RETRY_WAIT_S, SLEEP, TIMEOUT, YAHOO_HOSTS, _error_description, _last_host, _one_get, _pace, _url_interval, _url_symbol, _yahoo_error
#   _ERROR_CODES: -
#   fetch_chart: NoData, YAHOO_HOSTS, _ERROR_CODES, _yahoo_error, chart_url, yahoo_get
#   parse_chart: -
#   spark: SPARK_MAX, YAHOO_HOSTS, parse_chart, ts_to_utc, yahoo_get
#   FETCH_LOG_MAX: -
#   cache_file: -
#   load_cache: cache_file
#   save_cache: -
#   last_fetch: -
#   merge_rows: LOOKBACK, utc_to_ts
#   stored_rows: -
#   refresh: FIRST_RANGE, LOOKBACK, MIN_REFRESH_S, STEP_S, fetch_into, last_fetch, load_cache, parse_utc, utc_to_ts
#   fetch_into: FETCH_LOG_MAX, _last_host, cache_file, fetch_chart, merge_rows, parse_chart, save_cache, stored_rows, utc_iso
#   DONE_MARGIN_MIN: -
#   OUTAGE_MIN_SLOTS: -
#   COARSE: -
#   COARSE_MIN_STOP_PIPS: -
#   COARSE_MARGIN_PIPS: -
#   STALE_MINUTES: -
#   YAHOO_DECIMALS: -
#   FUTURES_KIND: -
#   symbol_root: -
#   yahoo_inst: FUTURES_KIND, YAHOO_DECIMALS, symbol_root
#   inst_root: symbol_root
#   yahoo_decimals: YAHOO_DECIMALS, inst_root
#   _round_dec: -
#   clean_rows: _round_dec, ts_to_utc, yahoo_decimals
#   _span_of: fx_day, open_spans
#   bar_done: DONE_MARGIN_MIN, STEP_S, _span_of
#   load_bars: STEP_S, _dec_clean, _span_of, adjust_rows, bar_done, clean_rows, inst_root, last_fetch, load_cache, parse_utc, ts_to_utc
#   _overlap: -
#   _touched_fx_days: fx_day_bounds, to_ny
#   open_time_in: _overlap, _touched_fx_days, open_spans
#   find_outages: OUTAGE_MIN_SLOTS, STEP_S, _touched_fx_days, hours_template, open_time_in, yahoo_no_data_cuts
#   _daily_flags: _touched_fx_days, early_close_utc, hours_template, open_spans, open_time_in
#   daily_from_hourly: _daily_flags, _day_forming, check_day_end, find_outages, fx_day, fx_day_bounds
#   _day_forming: _touched_fx_days, open_spans
#   data_age_min: -
#   _plan_bid: _dec_clean, _round_dec, yahoo_decimals
#   latest_price: STEP_S, _dec_clean, _plan_bid, _round_dec, data_age_min, last_fetch, load_bars, load_cache, parse_utc, ts_to_utc, yahoo_decimals
#   coarse_note: COARSE, COARSE_MIN_STOP_PIPS, _dec_clean
#   MONTH_CODES: -
#   ROLL_MATCH_BARS: -
#   ROLL_CHECK_HOURS: -
#   ROLL_SAME_DAYS: -
#   rolls_path: -
#   load_rolls_doc: load_json, rolls_path
#   load_rolls: load_rolls_doc
#   save_rolls: load_rolls_doc, rolls_path, save_json_atomic, utc_iso, utc_now
#   contract_from_name: MONTH_CODES
#   cme_business_day: HOLIDAYS
#   add_business_days: cme_business_day
#   _month_add: -
#   _last_business_day: _month_add, cme_business_day
#   roll_dates: _last_business_day, _month_add, add_business_days, cme_business_day
#   _cycle_contracts: -
#   _windows: _cycle_contracts, at_time, ny_to_utc, roll_dates
#   expected_front: _windows, utc_iso
#   expected_rolls: _windows
#   _roll_bar: _round_dec, ts_to_utc
#   adjust_rows: YAHOO_DECIMALS, _roll_bar, parse_utc
#   ROLL_AWAY: -
#   _get_path: -
#   _set_path: -
#   rebase: ROLL_AWAY, _dec_clean, _get_path, _set_path, inst_root, round_away, round_price
#   ROLL_WORDS: -
#   roll_tolerance: yahoo_decimals
#   _contract_bars: NoData, clean_rows, fetch_into, load_cache, utc_to_ts
#   _switch_bar: -
#   _roll_ratio: -
#   _newest_fetch: last_fetch, load_cache
#   _name_change: load_cache
#   _bz_still_old: ROLL_MATCH_BARS, _contract_bars, roll_tolerance
#   check_roll_forward: NoData, ROLL_CHECK_HOURS, ROLL_WORDS, _bz_still_old, _contract_bars, _keep_roll, _name_change, _roll_ratio, _switch_bar, add_business_days, at_time, clean_rows, contract_from_name, expected_rolls, load_cache, load_rolls_doc, ny_to_utc, parse_utc, roll_tolerance, save_rolls, to_ny, utc_iso, utc_to_ts
#   _keep_roll: ROLL_SAME_DAYS, parse_utc, save_rolls
#   HISTORY_PRE_DAYS: -
#   HISTORY_MIN_SIGMA: -
#   HISTORY_MIN_TAU: -
#   _median: -
#   _settle_utc: at_time, city_to_utc
#   _settle_value: _settle_utc, at_time, early_close_utc, ny_to_utc
#   _daily_closes: clean_rows, load_cache, refresh, to_ny
#   _gap_run: -
#   _history_switch_bar: _settle_utc, add_business_days
#   _history_window: HISTORY_MIN_SIGMA, HISTORY_MIN_TAU, HISTORY_PRE_DAYS, _gap_run, _history_switch_bar, _median, _settle_utc, _settle_value, add_business_days, cme_business_day, to_ny, utc_iso
#   find_rolls_history: _daily_closes, _history_window, _keep_roll, clean_rows, expected_rolls, load_cache, load_rolls_doc, save_rolls, utc_iso
#   ROLL_GUARD_ATR: -
#   roll_guard: ATR_WINDOW, ROLL_GUARD_ATR, ROLL_WORDS, atr_wilder, expected_rolls, inst_root, parse_utc, usable_bars
#   K_LIMITS: -
#   BROKER_PRICE_MINUTES: -
#   broker_k: K_LIMITS, _dec_clean, _price_dec
#   to_broker: _dec_clean, round_price
#   to_broker_away: _dec_clean, round_away, to_broker
#   saved_broker_price: BROKER_PRICE_MINUTES, fx_day, parse_utc
#   delay_error: ATR_WINDOW, _round_dec, atr_wilder, usable_bars
# group money
#   RATE_PAIRS: -
#   MIN_STOP_SPREADS: -
#   GAP_ATR: -
#   GAP_ATR_DAY: -
#   TooSmall: -
#   _cents: -
#   _num_word: _dec_clean
#   _pct_word: _num_word
#   _pccy: -
#   _is_currency: RATE_PAIRS
#   _rate_from_price: RATE_PAIRS, _price_dec
#   rate_entry: RATE_PAIRS, _price_dec, _rate_from_price
#   rate_age_h: ny_to_utc, to_ny
#   fx_rates: RATE_PAIRS, instrument, latest_price, parse_utc, rate_age_h, rate_entry, utc_iso
#   usd_per_unit: RATE_PAIRS, _dec_clean, _is_currency, _num_word, _rate_from_price
#   _base_has_rate: _is_currency, _pccy
#   loss_usd_per_unit: _base_has_rate, _pccy, _price_dec, usd_per_unit
#   reward_usd_per_unit: _base_has_rate, _pccy, _price_dec, usd_per_unit
#   rate_symbols: RATE_PAIRS, _pccy
#   usd_per_point_per_lot: _dec_clean, _pccy, usd_per_unit
#   _swap_free: -
#   _margin_ccy_usd: RATE_PAIRS, _is_currency, _pccy, _price_dec, _rate_from_price, usd_per_unit
#   _commission: _dec_clean
#   _spread: _dec_clean, _pccy, usd_per_unit
#   _swap: _dec_clean, _is_currency, _margin_ccy_usd, _pccy, _price_dec, _swap_free, side_word, usd_per_unit
#   _fee: _dec_clean, _swap_free
#   commission_usd: _cents, _commission
#   spread_usd: _cents, _spread
#   swap_usd: _cents, _swap
#   swap_free_fee_usd: _cents, _fee
#   swap_nights: FULL_DAY_NAMES, ny_to_utc, to_ny
#   _costs: _commission, _fee, _swap
#   costs_usd: _cents, _costs
#   trade_costs: _cents, _commission, _fee, _spread, _swap
#   _lots_dec: _dec_clean
#   _check_stop_side: _price_dec, side_word
#   max_loss_core: _check_stop_side, _costs, _dec_clean, loss_usd_per_unit
#   max_loss_usd: _cents, _lots_dec, max_loss_core
#   net_core: _check_stop_side, _costs, _dec_clean, _price_dec, max_loss_core, reward_usd_per_unit
#   reward_risk_net: net_core
#   r_multiple: _dec_clean, max_loss_usd, result_usd
#   _pl_core: _dec_clean, _fee, _pccy, _price_dec, _swap, side_word, usd_per_unit
#   result_core: _commission, _pl_core
#   result_usd: _cents, _lots_dec, result_core
#   position_pl_usd: _cents, _lots_dec, _pl_core, ask_of
#   _margin: _dec_clean, _lots_dec, _margin_ccy_usd, _price_dec, side_word
#   margin_usd: _cents, _margin
#   margin_level_pct: _dec_clean
#   margin_state: _cents, _dec_clean, margin_level_pct
#   _floor_lots: _dec_clean
#   _lot_word: _dec_clean
#   _risk_words: _lot_word, money
#   _loss_per_lot: _dec_clean, _price_dec, loss_usd_per_unit
#   lots_for_risk: TooSmall, _cents, _dec_clean, _floor_lots, _loss_per_lot, _risk_words
#   SIZE_LIMITS: -
#   _risk_money: _dec_clean, _pct_word, money
#   size_order: SIZE_LIMITS, TooSmall, _cents, _check_stop_side, _costs, _dec_clean, _floor_lots, _loss_per_lot, _lot_word, _margin, _num_word, _pct_word, _risk_money, _risk_words, money
#   _points_word: -
#   stops_problems: _dec_clean, _points_word, _price_dec, ask_of, price_text, side_word, to_points
#   bar_hits: _dec_clean, side_word, spread_price
#   pending_fill: _dec_clean, _price_dec, ask_of, round_price, side_word, spread_price
#   gap_fill: _dec_clean, round_price, side_word, spread_price
# group indicators, setups
#   SKIP_FLAGS: -
#   ATR_WINDOW: -
#   usable_bars: SKIP_FLAGS
#   true_range: -
#   atr_wilder: true_range
#   sma: -
#   ema_series: -
#   ema: ema_series
#   rsi_series: -
#   rsi_wilder: rsi_series
#   highest: -
#   lowest: -
#   NEAR_ATR: -
#   CHASE_ATR: -
#   MIN_DAILY_BARS: -
#   SWING_BUFFER_ATR: -
#   SWING_VALID_DAYS: -
#   OR_MAX_DAILY_ATR: -
#   OR_MIN_SPREADS: -
#   NEAR_ATR_5M: -
#   MIN_STOP_ATR: -
#   MIN_DAY_MINUTES_LEFT: -
#   SIGNAL_BANNED: -
#   _setup: -
#   SETUPS: _setup
#   _flag_level: highest, lowest
#   _swing_level: _flag_level, ema, ema_series, highest, lowest, rsi_series, sma
#   coarse_words: COARSE
#   find_setups: ATR_WINDOW, CHASE_ATR, MIN_DAILY_BARS, NEAR_ATR, SETUPS, SKIP_FLAGS, _swing_level, atr_wilder, coarse_words, day_label, parse_fx_date, round_price, usable_bars
#   opening_range: opening_range_window, round_price, utc_iso
#   or_trigger: parse_utc
#   CORRELATION_GROUPS: -
#   group_of: CORRELATION_GROUPS
#   currency_legs: -
# group files and config
#   CONFIG_NOTE: -
#   SECRET_KEYS: -
#   MERGED_KEYS: -
#   D15_DAYS: -
#   KINDS: -
#   PRICE_SOURCES: -
#   CALC_MODES: -
#   SWAP_MODES: -
#   TRIPLE_DAYS: -
#   PROFIT_CURRENCIES: -
#   FUTURES_KEYS: -
#   FUTURES_SUFFIXES: -
#   FUTURES_MONTH_LETTERS: MONTH_CODES
#   ROLL_RULES: -
#   SWING_STOP_RULES: -
#   DAY_STOP_RULES: -
#   SESSION_WORDS: -
#   NEEDED_COLUMNS: -
#   COLUMN_DEFAULTS: -
#   BUILTIN_INSTRUMENTS: -
#   DEFAULTS: BUILTIN_INSTRUMENTS
#   _cfg_get: -
#   _cfg_put: -
#   _is_number: -
#   _num_text: -
#   _is_hhmm: -
#   _cfg_bad: -
#   _cfg_bool: _cfg_bad, _cfg_get
#   _cfg_number: _cfg_bad, _cfg_get, _is_number, _num_text
#   _cfg_int: _cfg_bad, _cfg_get, _cfg_put, _is_number, _num_text
#   _cfg_choice: _cfg_bad, _cfg_get
#   _cfg_hhmm: _cfg_get, _is_hhmm
#   _cfg_keys: _cfg_bad, _cfg_get
#   _rule_k: -
#   _is_date: -
#   _check_account: SERVER_TIME_BAD, _cfg_bool, _cfg_choice, _cfg_get, _cfg_number, server_offset
#   _check_top: DAY_ENDS, _cfg_bool, _cfg_choice, _cfg_keys
#   _check_risk: _cfg_bool, _cfg_int, _cfg_keys, _cfg_number
#   _cfg_target: _cfg_bad, _cfg_get, _rule_k
#   _cfg_stop: _cfg_bad, _cfg_get, _rule_k
#   _check_swing: SWING_STOP_RULES, _cfg_choice, _cfg_get, _cfg_hhmm, _cfg_int, _cfg_stop, _cfg_target
#   _check_day: DAY_STOP_RULES, _cfg_choice, _cfg_get, _cfg_hhmm, _cfg_int, _cfg_stop, _cfg_target, _check_day_times, _is_number
#   _pkt_hhmm: to_pkt
#   _check_day_times: D15_DAYS, SESSION_WORDS, _pkt_hhmm, at_time, fx_day_bounds, ny_to_utc, opening_range_window, parse_fx_date, pkt_time_on_fx_day
#   _check_news: _cfg_bad, _cfg_get, _cfg_int
#   _check_dates: _cfg_number, _is_date, _is_hhmm
#   _plain: _is_number
#   _row_bad: _plain
#   _row_number: _is_number, _num_text, _row_bad
#   _row_int: _is_number, _num_text, _row_bad
#   _row_choice: _row_bad
#   _inst_size: KINDS, PRICE_SOURCES, _dec_clean, _is_number, _plain, _row_choice, _row_int, _row_number
#   _inst_money: BROKER_TEMPLATES, CALC_MODES, DATA_TEMPLATES, SWAP_MODES, TRIPLE_DAYS, _is_number, _row_choice, _row_int, _row_number
#   _inst_futures: FUTURES_KEYS, FUTURES_MONTH_LETTERS, FUTURES_SUFFIXES, ROLL_RULES, _is_hhmm, _row_bad
#   _check_instrument: BUILTIN_INSTRUMENTS, NEEDED_COLUMNS, PROFIT_CURRENCIES, _inst_futures, _inst_money, _inst_size
#   check_config: MERGED_KEYS, _check_account, _check_dates, _check_day, _check_instrument, _check_news, _check_risk, _check_swing, _check_top
#   _find_secret: SECRET_KEYS
#   _fill_derived: _is_number
#   merge_config: COLUMN_DEFAULTS, DEFAULTS, MERGED_KEYS, _fill_derived
#   config_warnings: DEFAULTS, server_offset
#   load_config: CONFIG_NOTE, _find_secret, check_config, config_warnings, load_json, merge_config
# group shared by skill scripts
#   RECENT_FX_DAYS: -
#   WRAP: -
#   WIDTH: -
#   back_fx_day: full_fx_holiday, parse_fx_date
#   recent_start: RECENT_FX_DAYS, back_fx_day, fx_day_bounds
#   contract_month: MONTH_CODES
#   roll_window_date: to_ny
#   fx_day_of: fx_day, to_ny
#   rolls_known: parse_utc
#   roll_notes: RECENT_FX_DAYS, ROLL_WORDS, _daily_closes, add_fx_days, contract_month, day_label, expected_rolls, fx_day, fx_day_bounds, fx_day_of, inst_root, parse_fx_date, parse_utc, recent_start, roll_window_date, to_ny
#   pct_text: -
#   fresh_quote: latest_price, load_json, parse_utc


# ---------------------------------------------------------------- files and config, words
# group: files and config, words

ORDER_FIELDS = ("symbol", "side", "intent", "lots", "type", "entry", "stop_loss",
                "take_profit", "expiry", "style")
FRESH_CHECK_MINUTES = 5


def load_json(path, default=None):
    """Read a UTF-8 JSON file. A missing file gives default. An empty or broken file
    is an error (never repaired), so a broken ledger never looks like "no trades"."""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise ValueError("could not read %s: %s" % (path, exc.strerror or exc))
    if not raw:
        raise ValueError("%s is empty" % path)
    try:
        return json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError("%s is not valid JSON: %s" % (path, exc))
    except ValueError as exc:
        raise ValueError("%s is not valid JSON: %s" % (path, exc))


def save_json_atomic(path, doc):
    """Write doc as JSON (indent 2, a final newline) to path + ".tmp", then move it
    onto path in one step. Makes the folder first. No .tmp file is left on success."""
    tmp = path + ".tmp"
    try:
        folder = os.path.dirname(os.path.abspath(path))
        os.makedirs(folder, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(doc, indent=2, sort_keys=False, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except OSError as exc:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise ValueError("could not save %s: %s" % (path, exc.strerror or exc))


def fx_paths(work_dir, data_dir=None, config=None):
    """Every path of the fx/ folder as a dict. work_dir is the fx/ folder itself;
    data_dir defaults to work_dir/data and config to work_dir/config.json."""
    w = work_dir
    d = data_dir or os.path.join(w, "data")
    paths = {"config": config or os.path.join(w, "config.json")}
    for key in ("alerts", "watchlist", "entry", "plan", "timemachine", "orders",
                "prices", "portfolio"):
        paths[key] = os.path.join(w, key + ".json")
    for key in ("risk", "broker", "alerts", "news", "cot"):
        paths[key + "_state"] = os.path.join(w, "state", key + ".json")
    paths["data"] = d
    paths["yahoo"] = os.path.join(d, "yahoo")
    paths["rolls"] = os.path.join(d, "rolls.json")
    for key in ("calendar", "cot", "news"):
        paths[key] = os.path.join(d, key)
    return paths


def fingerprint(order):
    """The order's hash (D18, the earlier pack's rule unchanged): the ORDER_FIELDS that are
    present and not null, as sorted compact JSON, sha256, the first 16 hex characters."""
    core = {k: order[k] for k in ORDER_FIELDS if k in order and order[k] is not None}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def load_doc(path, what, skill, required=True):
    """Read one fx/ JSON file with the common checks (part 01 1.3): it must exist
    (when required; else None) and be a JSON object. what = "plan", skill = "fx-exit"."""
    doc = load_json(path)
    if doc is None:
        if required:
            raise ValueError("no %s at %s. Run %s first." % (what, path, skill))
        return None
    if not isinstance(doc, dict):
        raise ValueError("%s must be a JSON object: {...}" % path)
    return doc


def check_objects(doc, key, path):
    """The list under doc[key], which must be a list of JSON objects."""
    items = doc.get(key)
    if not isinstance(items, list) or not all(isinstance(x, dict) for x in items):
        raise ValueError('%s: "%s" must be a list of JSON objects: [{...}]' % (path, key))
    return items


def check_symbol(symbol, cfg, path):
    """The symbol, which must be a key of cfg["instruments"]."""
    if not isinstance(symbol, str) or symbol not in cfg.get("instruments", {}):
        raise ValueError('%s: "%s" is not in your instrument list (fx/config.json "instruments")'
                         % (path, symbol))
    return symbol


def check_side(side, path):
    """The side, which must be "buy" or "sell"."""
    if side not in ("buy", "sell"):
        raise ValueError('%s: side must be "buy" or "sell", not "%s"' % (path, side))
    return side


def check_style(style, path):
    """The style, which must be "swing" or "day"."""
    if style not in ("swing", "day"):
        raise ValueError('%s: style must be "swing" or "day", not "%s"' % (path, style))
    return style


def check_utc(doc, key, path):
    """doc[key] as a naive UTC datetime; it must be text like 2026-11-11T08:40:00Z."""
    text = doc.get(key)
    try:
        return datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        raise ValueError('%s: "%s" is not a UTC time like 2026-11-11T08:40:00Z' % (path, key))


def _positive_number(x):
    """True when x is a finite int or float above 0 (never a bool or text)."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return False
    return math.isfinite(x) and x > 0


def check_price(doc, key, path):
    """doc[key], which must be a price (a number above 0)."""
    x = doc.get(key)
    if not _positive_number(x):
        raise ValueError('%s: "%s" must be a price above 0, not %s' % (path, key, x))
    return x


def check_lots(lots, path):
    """The lots, which must be a number above 0."""
    if not _positive_number(lots):
        raise ValueError("%s: lots must be a number above 0, not %s" % (path, lots))
    return lots


def check_close_order(order, n, path):
    """A close order (intent "close"): entry, stop_loss, take_profit and expiry null,
    type market, position_id set. n is the order's number (from 1)."""
    ok = (all(order.get(k) is None for k in ("entry", "stop_loss", "take_profit", "expiry"))
          and order.get("type") == "market" and bool(order.get("position_id")))
    if not ok:
        raise ValueError("%s: order %s closes a position. Leave entry, stop_loss, "
                         "take_profit and expiry empty (null). Use type market. Set "
                         "position_id." % (path, n))
    return order


# ---- words (part 06 1): plain text for people. None reads a file or the clock.

JSON_VERSION = 1


def _words_dec(x, what):
    """x as a Decimal for the words helpers; ValueError "Not <what>: X" for None,
    text, a bool, NaN or infinity."""
    if isinstance(x, bool) or not isinstance(x, (int, float, Decimal)):
        raise ValueError("Not %s: %s" % (what, x))
    d = Decimal(str(x))
    if not d.is_finite():
        raise ValueError("Not %s: %s" % (what, x))
    return d


def money(usd):
    """US dollars for people: "$1,234.56", "-$12.30". Always 2 decimals, half up;
    -0.004 gives "$0.00" (never "-$0.00")."""
    v = _words_dec(usd, "an amount of money").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return "%s$%s" % ("-" if v < 0 else "", format(abs(v), ",f"))


def signed_money(usd):
    """As money, with "+" above 0 after rounding: "+$12.30", "-$12.30", "$0.00"."""
    v = _words_dec(usd, "an amount of money").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return ("+" if v > 0 else "") + money(v)


def price_text(price, inst):
    """A price with all the instrument's digits kept and thousands commas:
    "2,433.40" for gold, "1.08450" for EURUSD, "-3.50" for oil below zero."""
    d = _words_dec(price, "a price")
    q = d.quantize(Decimal(1).scaleb(-int(inst["digits"])), rounding=ROUND_HALF_UP)
    return "%s%s" % ("-" if q < 0 else "", format(abs(q), ",f"))


def lots_text(lots):
    """Lots for people: at least 2 decimals, more only when needed, then the word:
    "1.00 lot", "0.86 lots", "0.005 lots"."""
    d = _words_dec(lots, "a number of lots")
    if d < 0:
        raise ValueError("Lots cannot be below 0.")
    d = d.normalize()
    if d.as_tuple().exponent > -2:
        d = d.quantize(Decimal("0.01"))
    return "%s %s" % (format(d, "f"), "lot" if d == 1 else "lots")


def side_word(side):
    """"buy" -> "BUY", "sell" -> "SELL" (the words on the MetaTrader buttons)."""
    if side == "buy":
        return "BUY"
    if side == "sell":
        return "SELL"
    raise ValueError('Side must be "buy" or "sell", not %s.' % (side,))


def age_text(minutes):
    """How old a price is: "live" under 1 minute, "11 min old", "2 h 5 min old",
    "3 h old", "2 days old". None gives "age unknown"; below 0 counts as 0."""
    if minutes is None:
        return "age unknown"
    m = float(_words_dec(minutes, "a number of minutes"))
    m = max(m, 0.0)
    if m < 1:
        return "live"
    if m < 60:
        return "%d min old" % int(m)
    if m < 1440:
        h, mins = divmod(int(m), 60)
        return "%d h old" % h if mins == 0 else "%d h %d min old" % (h, mins)
    days = int(m // 1440)
    return "1 day old" if days == 1 else "%d days old" % days


def scale_note(inst):
    """Whose price it is: "Yahoo price, close to your broker's price" for spot FX,
    "Yahoo price (gold futures), not your broker's price" for a futures row."""
    source = inst.get("price_source")
    if source == "spot_fx":
        return "Yahoo price, close to your broker's price"
    if source == "futures":
        name = str(inst.get("name") or inst.get("key") or "")
        if name and not (len(name) > 1 and name[1].isupper()):
            name = name[0].lower() + name[1:]
        return "Yahoo price (%s futures), not your broker's price" % name
    raise ValueError('price_source must be "spot_fx" or "futures", not %s.' % (source,))


def broker_note():
    """The words next to a price the user typed from the broker."""
    return "your broker's price (you typed it)"


def both_times(utc):
    """Karachi and New York time in one line: "Thu 12:40 PM PKT (3:40 AM New York)"."""
    return "%s (%s)" % (pkt_label(utc), ny_label(utc))


def add_common(parser, write=False):
    """Add the common flags (part 06 2.1) to one argparse parser; write=True also adds
    --write. Path defaults are None: fx_paths then uses fx/config.json and fx/data."""
    parser.add_argument("--config", metavar="FILE", default=None,
                        help="your config (default fx/config.json)")
    parser.add_argument("--data", metavar="DIR", default=None,
                        help="the data folder (default fx/data)")
    parser.add_argument("--offline", action="store_true",
                        help="never use the network; read only the data folder")
    parser.add_argument("--now", metavar='"YYYY-MM-DD HH:MM"', default=None,
                        help="pretend it is this Karachi time (or ...Z for UTC); also means --offline")
    parser.add_argument("--json", action="store_true", help="print one JSON object")
    parser.add_argument("--quiet", action="store_true", help="print only the result lines")
    if write:
        parser.add_argument("--write", action="store_true", help="also write the output file")
    return parser


def fail(err, tool):
    """Print "<tool>: <message>" on stderr as one line and return 2 (an error)."""
    text = " ".join(str(err).split())
    print("%s: %s" % (tool, text), file=sys.stderr)
    return 2


def emit_json(doc):
    """Print doc as one JSON object (indent 2, sorted keys)."""
    print(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False))


# ---------------------------------------------------------------- instruments and rounding
# group: instruments and rounding


def _dec_clean(x):
    """x as a Decimal with float noise under a billionth cleaned (half up), so
    1.1234999999999 counts as 1.1235. ValueError "not a number: x" when not finite."""
    if isinstance(x, bool) or x is None:
        raise ValueError("not a number: %r" % (x,))
    try:
        d = Decimal(str(x))
        if not d.is_finite():
            raise ValueError("not a number: %r" % (x,))
        return d.quantize(Decimal("0.000000001"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError):
        raise ValueError("not a number: %r" % (x,))


def _price_dec(x):
    """A price as a cleaned Decimal; ValueError when it is not above 0."""
    d = _dec_clean(x)
    if d <= 0:
        raise ValueError("A price must be above 0.")
    return d


def instrument(cfg, key):
    """The instrument row cfg["instruments"][key] as a NEW dict, with the defaults
    filled (key, broker_symbol, point, tick_size, pip ...) and its lot and tick checks."""
    rows = cfg.get("instruments") or {}
    if key not in rows:
        near = difflib.get_close_matches(str(key), list(rows), n=1, cutoff=0.6)
        such = near[0] if near else ("XAUUSD" if "XAUUSD" in rows or not rows else sorted(rows)[0])
        raise ValueError('Unknown instrument "%s". Use a key from your config, such as %s. '
                         'Put your broker\'s name in "broker_symbol".' % (key, such))
    inst = copy.deepcopy(rows[key])
    inst["key"] = key
    if not inst.get("broker_symbol"):
        inst["broker_symbol"] = key
    digits = int(inst["digits"])
    point = Decimal(1).scaleb(-digits)
    if inst.get("point") is not None and _dec_clean(inst["point"]) != point:
        raise ValueError("%s: point %s does not match digits %s. Copy both from MetaTrader."
                         % (key, inst["point"], digits))
    inst["point"] = float(point)
    if inst.get("tick_size") is None:
        inst["tick_size"] = float(point)
    if inst.get("pip") is None:
        if inst.get("kind") == "fx":
            inst["pip"] = 0.01 if inst.get("quote") == "JPY" else 0.0001
        else:
            inst["pip"] = None
    for name in ("leverage", "initial_margin", "swap_free_fee_per_lot", "futures"):
        inst.setdefault(name, None)
    for name in ("margin_rate", "yahoo_to_broker"):
        if inst.get(name) is None:
            inst[name] = 1.0
    for name in ("swap_free_grace_nights", "commission_per_lot_side", "stops_level_points"):
        if inst.get(name) is None:
            inst[name] = 0
    sizes = [_dec_clean(inst[n]) for n in ("contract_size", "lot_step", "min_lot", "max_lot")]
    if min(sizes) <= 0 or sizes[2] > sizes[3]:
        raise ValueError("%s: contract_size, min_lot, max_lot and lot_step must be above 0." % key)
    if sizes[2] % sizes[1] != 0:
        raise ValueError("%s: min_lot %s is not a whole number of lot_step %s."
                         % (key, inst["min_lot"], inst["lot_step"]))
    tick = _dec_clean(inst["tick_size"])
    if tick <= 0 or tick % point != 0:
        raise ValueError("%s: tick_size %s does not fit digits %s." % (key, inst["tick_size"], digits))
    return inst


def _to_tick(d, inst, rounding):
    """A cleaned Decimal price to a whole number of ticks (the given rounding), then
    quantized to the instrument's digits, as a float."""
    tick = _dec_clean(inst["tick_size"])
    q = (d / tick).to_integral_value(rounding=rounding) * tick
    return float(q.quantize(Decimal(1).scaleb(-int(inst["digits"])), rounding=ROUND_HALF_UP))


def round_price(price, inst):
    """A price to the nearest tick, half up (entries, fills, plain levels):
    EURUSD 1.124905 -> 1.12491, XAUUSD 4134.175 -> 4134.18."""
    return _to_tick(_price_dec(price), inst, ROUND_HALF_UP)


def round_away(price, entry, inst):
    """A stop or a target to the tick AWAY from the entry, so the risk never shrinks
    and the target never comes closer: below the entry down, above it up."""
    p = _price_dec(price)
    e = _price_dec(entry)
    if p == e:
        raise ValueError("A stop or a target cannot be at the entry price.")
    return _to_tick(p, inst, ROUND_FLOOR if p < e else ROUND_CEILING)


def to_points(distance, inst):
    """A price distance in whole points, half up, the sign kept:
    to_points(-0.00141, EURUSD) = -141."""
    d = _dec_clean(distance) / _dec_clean(inst["point"])
    return int(d.to_integral_value(rounding=ROUND_HALF_UP))


def from_points(points, inst):
    """Points back to a price distance, quantized to digits: from_points(9, XAUUSD) = 0.09."""
    d = _dec_clean(points) * _dec_clean(inst["point"])
    return float(d.quantize(Decimal(1).scaleb(-int(inst["digits"])), rounding=ROUND_HALF_UP))


def pips_text(distance, inst):
    """A distance in plain words, sign dropped: "14.1 pips (141 points)" for FX,
    "174.51 (17,451 points)" for gold and the other non-FX rows (no pip word)."""
    d = abs(_dec_clean(distance))
    pts = abs(to_points(d, inst))
    pts_word = "%s %s" % (format(pts, ","), "point" if pts == 1 else "points")
    if inst.get("pip"):
        pips = (d / _dec_clean(inst["pip"])).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        return "%s %s (%s)" % (format(pips, ",f"), "pip" if pips == 1 else "pips", pts_word)
    q = d.quantize(Decimal(1).scaleb(-int(inst["digits"])), rounding=ROUND_HALF_UP)
    return "%s (%s)" % (format(q, ",f"), pts_word)


def spread_price(inst):
    """The table spread as a price distance: spread_points x point, exact (not rounded
    to the tick), on the plan scale. EURUSD 1 point = 0.00001, XAUUSD 9 points = 0.09."""
    return float(_dec_clean(inst["spread_points"]) * _dec_clean(inst["point"]))


def ask_of(bid, inst):
    """The ask from a Yahoo bid (D5): bid + the table spread, to the nearest tick."""
    return round_price(_price_dec(bid) + Decimal(str(spread_price(inst))), inst)


# ---------------------------------------------------------------- clock, dst, fx day and sessions, market hours, calendar tables, economic calendar
# group: clock, dst, fx day and sessions, market hours, calendar tables, economic calendar

# ---- clock (part 03 section 1)

KARACHI_HOURS = 5   # Karachi time is UTC+5 all year: no daylight saving
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def utc_now():
    """The real clock as naive UTC, without microseconds. The only place that reads it."""
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)


def to_pkt(utc):
    """UTC -> naive Karachi time (UTC+5). An aware datetime is turned to UTC first."""
    if utc.tzinfo is not None:
        utc = (utc - utc.utcoffset()).replace(tzinfo=None)
    return utc + datetime.timedelta(hours=KARACHI_HOURS)


def pkt_to_utc(pkt):
    """Naive Karachi time -> naive UTC. An aware datetime keeps its own offset."""
    if pkt.tzinfo is not None:
        return (pkt - pkt.utcoffset()).replace(tzinfo=None)
    return pkt - datetime.timedelta(hours=KARACHI_HOURS)


def parse_now(text):
    """--now text -> naive UTC. Empty gives the real clock. No offset means Karachi time
    (a " PKT" at the end is fine); "...Z" is UTC; an offset like "+05:00" is honoured."""
    if not text:
        return utc_now()
    bad = '--now wants a time like "2026-07-23 12:40" (Karachi time) or ...Z (UTC)'   # spec 06 2.1 (check)
    t = str(text).strip()
    if t.upper().endswith("PKT"):
        t = t[:-3].strip()
    if t[-1:] in ("Z", "z"):
        t = t[:-1] + "+00:00"
    if not re.match(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}", t):
        raise ValueError(bad)
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise ValueError(bad)
    return pkt_to_utc(dt)


def utc_iso(utc):
    """naive UTC -> "2026-11-11T06:30:00Z" (how files store times). None stays None."""
    if utc is None:
        return None
    if utc.tzinfo is not None:
        utc = (utc - utc.utcoffset()).replace(tzinfo=None)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(text):
    """ISO time -> naive UTC. No offset means UTC; "Z" and offsets are honoured.
    Empty or bad text gives None (never raises)."""
    if not text or not isinstance(text, str):
        return None
    t = text.strip()
    if t[-1:] in ("Z", "z"):
        t = t[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)
    return dt


def ts_to_utc(seconds):
    """Unix seconds -> naive UTC. Never uses the machine's time zone."""
    return datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=int(seconds))


def utc_to_ts(utc):
    """naive UTC -> Unix seconds (int)."""
    return int((utc - datetime.datetime(1970, 1, 1)).total_seconds())


def clock_label(t):
    """datetime or time -> "3:05 PM" ("12:00 AM" is midnight)."""
    h = t.hour % 12 or 12
    return "%d:%02d %s" % (h, t.minute, "AM" if t.hour < 12 else "PM")


def day_label(d):
    """date or datetime -> "Wed 2026-11-11" (English day names always)."""
    if isinstance(d, datetime.datetime):
        d = d.date()
    return "%s %s" % (DAY_NAMES[d.weekday()], d.isoformat())


def short_date(d):
    """date or datetime -> "Wed 11 Nov" (no leading zero on the day; English names)."""
    return "%s %d %s" % (DAY_NAMES[d.weekday()], d.day, MONTH_NAMES[d.month - 1])


def pkt_label(utc, date=False):
    """naive UTC -> "Wed 3:05 PM PKT" (the Karachi day); date=True: "Wed 11 Nov 3:05 PM PKT"."""
    p = to_pkt(utc)
    day = short_date(p) if date else DAY_NAMES[p.weekday()]
    return "%s %s PKT" % (day, clock_label(p))


def ny_label(utc, date=False):
    """naive UTC -> "5:05 AM New York"; date=True: "Wed 11 Nov 5:05 AM New York"."""
    n = to_ny(utc)
    if date:
        return "%s %s New York" % (short_date(n), clock_label(n))
    return "%s New York" % clock_label(n)


def parse_hhmm(text, flag):
    """"23:30" or "9:05" -> datetime.time. Bad text: ValueError that names the flag or key."""
    if isinstance(text, datetime.time):
        return text
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(text or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError("%s wants a time like 23:30" % flag)
    return datetime.time(int(m.group(1)), int(m.group(2)))


# ---- dst (part 03 section 2)

def nth_weekday(year, month, weekday, n):
    """The n-th weekday (0 = Monday ... 6 = Sunday, n from 1) of a month, as a date."""
    d = datetime.date(year, month, 1)
    d += datetime.timedelta(days=(weekday - d.weekday()) % 7)
    d += datetime.timedelta(weeks=n - 1)
    if d.month != month:
        raise ValueError("there is no weekday %d number %d in %d-%02d" % (weekday, n, year, month))
    return d


def last_weekday(year, month, weekday):
    """The last weekday (0 = Monday ... 6 = Sunday) of a month, as a date."""
    nxt = datetime.date(year + month // 12, month % 12 + 1, 1)
    d = nxt - datetime.timedelta(days=1)
    return d - datetime.timedelta(days=(d.weekday() - weekday) % 7)


def us_dst(utc):
    """True when New York is on summer time (EDT): 2nd Sunday of March 07:00 UTC to
    1st Sunday of November 06:00 UTC."""
    y = utc.year
    start = datetime.datetime.combine(nth_weekday(y, 3, 6, 2), datetime.time(7))
    end = datetime.datetime.combine(nth_weekday(y, 11, 6, 1), datetime.time(6))
    return start <= utc < end


def uk_dst(utc):
    """True when London is on summer time (BST): last Sunday of March 01:00 UTC to
    last Sunday of October 01:00 UTC."""
    y = utc.year
    start = datetime.datetime.combine(last_weekday(y, 3, 6), datetime.time(1))
    end = datetime.datetime.combine(last_weekday(y, 10, 6), datetime.time(1))
    return start <= utc < end


def sydney_dst(utc):
    """True when Sydney is on summer time (AEDT). It ends on the 1st Sunday of April and
    starts on the 1st Sunday of October, both at Saturday 16:00 UTC."""
    y = utc.year
    eight = datetime.timedelta(hours=8)
    april_end = datetime.datetime.combine(nth_weekday(y, 4, 6, 1), datetime.time(0)) - eight
    october_start = datetime.datetime.combine(nth_weekday(y, 10, 6, 1), datetime.time(0)) - eight
    return not (april_end <= utc < october_start)


def to_ny(utc):
    """naive UTC -> New York wall clock."""
    return utc - datetime.timedelta(hours=4 if us_dst(utc) else 5)


def ny_to_utc(local):
    """New York wall clock -> naive UTC. A doubled hour gives the first (summer) one;
    a missing hour is read with the winter offset."""
    a = local + datetime.timedelta(hours=4)
    return a if us_dst(a) else local + datetime.timedelta(hours=5)


def to_london(utc):
    """naive UTC -> London wall clock."""
    return utc + datetime.timedelta(hours=1) if uk_dst(utc) else utc


def london_to_utc(local):
    """London wall clock -> naive UTC (a doubled hour gives the summer one)."""
    a = local - datetime.timedelta(hours=1)
    return a if uk_dst(a) else local


def to_sydney(utc):
    """naive UTC -> Sydney wall clock."""
    return utc + datetime.timedelta(hours=11 if sydney_dst(utc) else 10)


def sydney_to_utc(local):
    """Sydney wall clock -> naive UTC (a doubled hour gives the summer one)."""
    a = local - datetime.timedelta(hours=11)
    return a if sydney_dst(a) else local - datetime.timedelta(hours=10)


def to_tokyo(utc):
    """naive UTC -> Tokyo wall clock (UTC+9, no daylight saving)."""
    return utc + datetime.timedelta(hours=9)


def tokyo_to_utc(local):
    """Tokyo wall clock -> naive UTC."""
    return local - datetime.timedelta(hours=9)


def city_to_utc(city, local):
    """A wall clock time in "ny", "london", "sydney" or "tokyo" -> naive UTC."""
    if city == "ny":
        return ny_to_utc(local)
    if city == "london":
        return london_to_utc(local)
    if city == "sydney":
        return sydney_to_utc(local)
    if city == "tokyo":
        return tokyo_to_utc(local)
    raise ValueError('A city is "ny", "london", "sydney" or "tokyo", not "%s"' % city)


SERVER_TIME_BAD = ('"account.server_time" must be "ny+7" (GMT+2 in winter, GMT+3 in summer) '
                   'or like "utc+0"')


def server_offset(server_cfg):
    """"ny+7" -> None; "utc+3" -> 3; "utc-2" -> -2. Anything else: ValueError."""
    m = re.match(r"^(ny\+7|utc([+-])(\d{1,2}))$", str(server_cfg or "").strip().lower())
    if not m:
        raise ValueError(SERVER_TIME_BAD)
    if m.group(1) == "ny+7":
        return None
    n = int(m.group(3))
    if n > 14:
        raise ValueError(SERVER_TIME_BAD)
    return n if m.group(2) == "+" else -n


def server_time(utc, server_cfg):
    """naive UTC -> the MetaTrader server clock ("ny+7": New York + 7 h; "utc+N": UTC + N h)."""
    n = server_offset(server_cfg)
    if n is None:
        return to_ny(utc) + datetime.timedelta(hours=7)
    return utc + datetime.timedelta(hours=n)


def server_to_utc(local, server_cfg):
    """A time in the MetaTrader server clock -> naive UTC (for an expiry the user types)."""
    n = server_offset(server_cfg)
    if n is None:
        return ny_to_utc(local - datetime.timedelta(hours=7))
    return local - datetime.timedelta(hours=n)


def odd_week(utc):
    """True in the weeks when only one of the US and the UK is on summer time."""
    return us_dst(utc) != uk_dst(utc)


# ---- fx day and sessions (part 03 sections 3 to 5)

DAY_ENDS = ("17:00 New York", "00:00 UTC")
FULL_DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
SESSIONS = {
    "sydney":   ("sydney", "07:00", "16:00"),
    "tokyo":    ("tokyo",  "09:00", "18:00"),
    "london":   ("london", "08:00", "17:00"),
    "new_york": ("ny",     "08:00", "17:00"),
}
OR_WINDOWS = {"london": ("london", "08:00"), "new_york": ("ny", "08:00"), "tokyo": ("tokyo", "09:00")}


def check_day_end(day_end):
    """Return day_end when it is "17:00 New York" or "00:00 UTC"; else ValueError."""
    if day_end not in DAY_ENDS:
        raise ValueError('"day_end" must be "17:00 New York" or "00:00 UTC"')
    return day_end


def parse_fx_date(fx_date, weekend_ok=False):
    """"2026-11-11" or a date -> datetime.date. A Saturday or Sunday is refused unless
    weekend_ok. Bad text: ValueError."""
    if isinstance(fx_date, datetime.datetime):
        d = fx_date.date()
    elif isinstance(fx_date, datetime.date):
        d = fx_date
    else:
        text = str(fx_date)
        try:
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", text):
                raise ValueError(text)
            d = datetime.date(int(text[:4]), int(text[5:7]), int(text[8:10]))
        except ValueError:
            raise ValueError('An FX day wants a date like "2026-11-11", not "%s"' % text)
    if not weekend_ok and d.weekday() >= 5:
        raise ValueError("%s is a %s. FX days are Monday to Friday." % (d.isoformat(), FULL_DAY_NAMES[d.weekday()]))
    return d


def at_time(d, hhmm):
    """A date and "HH:MM" (or a time) -> a naive datetime on that date."""
    return datetime.datetime.combine(d, parse_hhmm(hhmm, '"%s"' % hhmm))


def fx_day(utc, day_end):
    """The FX day (the New York date it ends on, or the UTC date) that holds this moment,
    as "YYYY-MM-DD"; None on a weekend."""
    check_day_end(day_end)
    n = to_ny(utc)
    if day_end == "17:00 New York":
        d = n.date() + datetime.timedelta(days=1 if n.hour >= 17 else 0)
        return None if d.weekday() >= 5 else d.isoformat()
    d = utc.date()
    wd = d.weekday()
    if wd == 5:
        return None
    if wd == 6:
        return (d + datetime.timedelta(days=1)).isoformat() if n.hour >= 17 and n.date() == d else None
    if wd == 4 and n.date() == d and n.hour >= 17:
        return None
    return d.isoformat()


def fx_day_bounds(fx_date, day_end):
    """(start_utc, end_utc) of an FX day. "00:00 UTC" days are clipped to the FX week
    (Monday starts Sunday 17:00 New York, Friday ends 17:00 New York)."""
    check_day_end(day_end)
    d = parse_fx_date(fx_date)
    day = datetime.timedelta(days=1)
    five = datetime.time(17)
    if day_end == "17:00 New York":
        return (ny_to_utc(datetime.datetime.combine(d - day, five)),
                ny_to_utc(datetime.datetime.combine(d, five)))
    start = datetime.datetime.combine(d, datetime.time(0))
    end = start + day
    if d.weekday() == 0:
        start = ny_to_utc(datetime.datetime.combine(d - day, five))
    if d.weekday() == 4:
        end = ny_to_utc(datetime.datetime.combine(d, five))
    return start, end


def full_fx_holiday(fx_date, cfg):
    """True when the date is in the config "holidays", or HOLIDAYS shuts "fx" all day."""
    return group_closed(fx_date, "fx", cfg)


def next_fx_day(fx_date, cfg):
    """The next Monday-to-Friday date after fx_date that is not a full FX holiday."""
    d = parse_fx_date(fx_date, weekend_ok=True)
    while True:
        d += datetime.timedelta(days=1)
        if d.weekday() < 5 and not full_fx_holiday(d, cfg):
            return d.isoformat()


def fx_days_between(a, b, cfg):
    """How many FX days d with a < d <= b (weekends and full FX holidays skipped).
    b before a gives the negative count."""
    da, db = parse_fx_date(a, weekend_ok=True), parse_fx_date(b, weekend_ok=True)
    if db < da:
        return -fx_days_between(db, da, cfg)
    n, d = 0, da
    while d < db:
        d += datetime.timedelta(days=1)
        if d.weekday() < 5 and not full_fx_holiday(d, cfg):
            n += 1
    return n


def add_fx_days(fx_date, n, cfg):
    """The FX day n FX days after fx_date (next_fx_day n times; n is 0 or more)."""
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise ValueError("add_fx_days wants a whole number of days, 0 or more, not %r" % (n,))
    d = parse_fx_date(fx_date, weekend_ok=True).isoformat()
    for _ in range(n):
        d = next_fx_day(d, cfg)
    return d


def last_fx_day(now, cfg):
    """The FX day of now; on a weekend, the Friday before."""
    day_end = (cfg or {}).get("day_end", "17:00 New York")
    d = fx_day(now, day_end)
    if d is not None:
        return d
    n = to_ny(now).date()
    back = (n.weekday() - 4) % 7
    if back == 0 and n.weekday() != 4:
        back = 7
    return (n - datetime.timedelta(days=back)).isoformat()


def fx_week_bounds(utc):
    """(open_utc, close_utc) of the FX week that holds utc (Sunday 17:00 to Friday 17:00
    New York), or of the next week when utc is in a weekend."""
    d = fx_day(utc, "17:00 New York")
    if d is None:
        n = to_ny(utc).date()
        monday = n + datetime.timedelta(days=(7 - n.weekday()) % 7 or 7)
    else:
        monday = parse_fx_date(d)
        monday -= datetime.timedelta(days=monday.weekday())
    five = datetime.time(17)
    return (ny_to_utc(datetime.datetime.combine(monday - datetime.timedelta(days=1), five)),
            ny_to_utc(datetime.datetime.combine(monday + datetime.timedelta(days=4), five)))


def pkt_time_on_fx_day(hhmm, fx_date, day_end):
    """The first moment at or after the FX day's start whose Karachi clock shows hhmm,
    as naive UTC (D15). It never raises past the day's end: the caller compares."""
    t = parse_hhmm(hhmm, '"%s"' % (hhmm,))
    start = fx_day_bounds(fx_date, day_end)[0]
    p = to_pkt(start)
    cand = datetime.datetime.combine(p.date(), t)
    if cand < p:
        cand += datetime.timedelta(days=1)
    return pkt_to_utc(cand)


def session_bounds(name, fx_date):
    """(start_utc, end_utc) of a trading session ("sydney", "tokyo", "london",
    "new_york") on the local calendar date equal to fx_date. Not clipped."""
    if name not in SESSIONS:
        raise ValueError('A session is "sydney", "tokyo", "london" or "new_york", not "%s"' % name)
    d = parse_fx_date(fx_date)
    city, a, b = SESSIONS[name]
    return city_to_utc(city, at_time(d, a)), city_to_utc(city, at_time(d, b))


def opening_range_window(session, fx_date, minutes):
    """(start_utc, end_utc) of the opening range: the session's local start on the local
    date fx_date, plus minutes. Bars that START inside it make the range."""
    if session not in OR_WINDOWS:
        raise ValueError('"day.or_session" must be "london", "new_york" or "tokyo"')
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes % 5 or not 5 <= minutes <= 120:
        raise ValueError('"day.or_minutes" must be a multiple of 5 (the bars are 5 minutes)')
    d = parse_fx_date(fx_date)
    city, hhmm = OR_WINDOWS[session]
    start = city_to_utc(city, at_time(d, hhmm))
    return start, start + datetime.timedelta(minutes=minutes)


# ---- calendar tables (part 03 sections 7 and 8)

TABLES_CHECKED_ON = "2026-11-10"   # re-check CME hours about 2 weeks before each holiday
TABLES_END = "2027-12-31"          # after this date holiday_note says the list has ended
TABLES_START = "2024-11-01"        # the 1h cache reaches 730 days back

# HOLIDAYS[FX date] = {name, closed, thin, no_data, provisional}; a missing list is [].
# Groups: "fx" (spot FX at the broker), "cme" (COMEX, NYMEX and all Yahoo futures data),
# "ice" (ICE Brent at the broker). no_data: Yahoo has no futures bars that New York day.
HOLIDAYS = {
    # past rows (for replays over the 1h cache)
    "2024-11-28": {"name": "Thanksgiving (US)", "thin": ["fx"], "no_data": ["cme"], "provisional": ["cme"]},
    "2024-11-29": {"name": "Day after Thanksgiving (US)", "thin": ["fx"]},
    "2024-12-24": {"name": "Christmas Eve", "thin": ["fx"]},
    "2024-12-25": {"name": "Christmas", "closed": ["fx", "cme", "ice"]},
    "2025-01-01": {"name": "New Year's Day", "closed": ["fx", "cme", "ice"]},
    "2025-01-20": {"name": "Martin Luther King Jr. Day (US)", "thin": ["fx"], "no_data": ["cme"], "provisional": ["cme"]},
    "2025-02-17": {"name": "Presidents' Day (US)", "thin": ["fx"], "no_data": ["cme"], "provisional": ["cme"]},
    "2025-04-18": {"name": "Good Friday", "closed": ["cme", "ice"], "thin": ["fx"]},
    "2025-05-26": {"name": "Memorial Day (US)", "thin": ["fx"], "no_data": ["cme"], "provisional": ["cme"]},
    "2025-06-19": {"name": "Juneteenth (US)", "thin": ["fx"], "no_data": ["cme"], "provisional": ["cme"]},
    "2025-07-04": {"name": "Independence Day (US)", "thin": ["fx"], "no_data": ["cme"], "provisional": ["cme"]},
    "2025-09-01": {"name": "Labor Day (US)", "thin": ["fx"], "no_data": ["cme"], "provisional": ["cme"]},
    "2025-11-27": {"name": "Thanksgiving (US)", "thin": ["fx"], "no_data": ["cme"]},
    "2025-11-28": {"name": "Day after Thanksgiving (US)", "thin": ["fx"]},
    "2025-12-24": {"name": "Christmas Eve", "thin": ["fx"]},
    "2025-12-25": {"name": "Christmas", "closed": ["fx", "cme", "ice"]},
    "2026-01-01": {"name": "New Year's Day", "closed": ["fx", "cme", "ice"]},
    "2026-01-19": {"name": "Martin Luther King Jr. Day (US)", "thin": ["fx"], "no_data": ["cme"]},
    "2026-02-16": {"name": "Presidents' Day (US)", "thin": ["fx"], "no_data": ["cme"]},
    "2026-04-03": {"name": "Good Friday", "closed": ["cme", "ice"], "thin": ["fx"]},
    "2026-05-25": {"name": "Memorial Day (US)", "thin": ["fx"], "no_data": ["cme"]},
    "2026-06-19": {"name": "Juneteenth (US)", "thin": ["fx"], "no_data": ["cme"]},
    "2026-07-03": {"name": "Independence Day (US, observed)", "thin": ["fx"], "no_data": ["cme"]},
    "2026-09-07": {"name": "Labor Day (US)", "thin": ["fx"], "no_data": ["cme"]},
    # from 2026-11-11 on
    "2026-11-11": {"name": "Veterans Day (US)"},
    "2026-11-26": {"name": "Thanksgiving (US)", "thin": ["fx"], "no_data": ["cme"]},
    "2026-11-27": {"name": "Day after Thanksgiving (US)", "thin": ["fx"]},
    "2026-12-24": {"name": "Christmas Eve", "thin": ["fx"]},
    "2026-12-25": {"name": "Christmas", "closed": ["fx", "cme", "ice"], "provisional": ["fx"]},
    "2026-12-28": {"name": "Boxing Day (UK)", "thin": ["fx"]},
    "2026-12-31": {"name": "New Year's Eve", "thin": ["fx"]},
    "2027-01-01": {"name": "New Year's Day", "closed": ["fx", "cme", "ice"], "provisional": ["fx"]},
    "2027-01-18": {"name": "Martin Luther King Jr. Day (US)", "thin": ["fx"], "no_data": ["cme"]},
    "2027-02-15": {"name": "Presidents' Day (US)", "thin": ["fx"], "no_data": ["cme"]},
    "2027-03-26": {"name": "Good Friday", "closed": ["cme", "ice"], "thin": ["fx"], "provisional": ["cme", "fx"]},
    "2027-03-29": {"name": "Easter Monday (UK)", "thin": ["fx"]},
    "2027-05-03": {"name": "Early May bank holiday (UK)", "thin": ["fx"]},
    "2027-05-31": {"name": "Memorial Day (US) and Spring bank holiday (UK)", "thin": ["fx"], "no_data": ["cme"]},
    "2027-06-18": {"name": "Juneteenth (US, observed)", "thin": ["fx"], "no_data": ["cme"]},
    "2027-07-05": {"name": "Independence Day (US, observed)", "thin": ["fx"], "no_data": ["cme"]},
    "2027-08-30": {"name": "Summer bank holiday (UK)", "thin": ["fx"]},
    "2027-09-06": {"name": "Labor Day (US)", "thin": ["fx"], "no_data": ["cme"]},
    "2027-10-11": {"name": "Columbus Day (US)"},
    "2027-11-11": {"name": "Veterans Day (US)"},
    "2027-11-25": {"name": "Thanksgiving (US)", "thin": ["fx"], "no_data": ["cme"]},
    "2027-11-26": {"name": "Day after Thanksgiving (US)", "thin": ["fx"]},
    "2027-12-24": {"name": "Christmas (US, observed)", "closed": ["cme"], "thin": ["fx"], "provisional": ["cme"]},
    "2027-12-27": {"name": "Christmas (UK, in lieu)", "thin": ["fx"]},
    "2027-12-28": {"name": "Boxing Day (UK, in lieu)", "thin": ["fx"]},
    "2027-12-31": {"name": "New Year (US, in lieu)", "thin": ["fx"], "provisional": ["cme"]},
}

# EARLY_CLOSES[date] = {name, cme: "HH:MM", ice: "HH:MM", provisional}: New York time on
# that calendar date; a group not named closes as usual.
EARLY_CLOSES = {
    # past rows (broker hours for replays; cme only, all provisional (check))
    "2024-11-28": {"name": "Thanksgiving (US)", "cme": "14:30", "provisional": ["cme"]},
    "2024-11-29": {"name": "Day after Thanksgiving (half day)", "cme": "14:45", "provisional": ["cme"]},
    "2024-12-24": {"name": "Christmas Eve (half day)", "cme": "13:45", "provisional": ["cme"]},
    "2025-01-20": {"name": "Martin Luther King Jr. Day", "cme": "14:30", "provisional": ["cme"]},
    "2025-02-17": {"name": "Presidents' Day", "cme": "14:30", "provisional": ["cme"]},
    "2025-05-26": {"name": "Memorial Day", "cme": "14:30", "provisional": ["cme"]},
    "2025-06-19": {"name": "Juneteenth", "cme": "13:00", "provisional": ["cme"]},
    "2025-07-03": {"name": "Day before Independence Day (half day)", "cme": "13:00", "provisional": ["cme"]},
    "2025-07-04": {"name": "Independence Day", "cme": "13:00", "provisional": ["cme"]},
    "2025-09-01": {"name": "Labor Day", "cme": "14:30", "provisional": ["cme"]},
    "2025-11-27": {"name": "Thanksgiving (US)", "cme": "14:30", "provisional": ["cme"]},
    "2025-11-28": {"name": "Day after Thanksgiving (half day)", "cme": "14:45", "provisional": ["cme"]},
    "2025-12-24": {"name": "Christmas Eve (half day)", "cme": "13:45", "provisional": ["cme"]},
    "2026-01-19": {"name": "Martin Luther King Jr. Day", "cme": "14:30", "provisional": ["cme"]},
    "2026-02-16": {"name": "Presidents' Day", "cme": "14:30", "provisional": ["cme"]},
    "2026-05-25": {"name": "Memorial Day", "cme": "14:30", "provisional": ["cme"]},
    "2026-06-19": {"name": "Juneteenth", "cme": "13:00", "provisional": ["cme"]},
    "2026-07-03": {"name": "Independence Day (observed)", "cme": "13:00", "provisional": ["cme"]},
    "2026-09-07": {"name": "Labor Day", "cme": "14:30", "provisional": ["cme"]},
    # from 2026-11-11 on
    "2026-11-26": {"name": "Thanksgiving (US)", "cme": "14:30", "ice": "13:30"},
    "2026-11-27": {"name": "Day after Thanksgiving", "cme": "14:45", "ice": "15:00"},
    "2026-12-24": {"name": "Christmas Eve", "cme": "13:45", "ice": "14:00", "provisional": ["cme"]},
    "2026-12-31": {"name": "New Year's Eve", "ice": "15:00"},
    "2027-01-18": {"name": "Martin Luther King Jr. Day", "cme": "14:30", "provisional": ["cme"]},
    "2027-02-15": {"name": "Presidents' Day", "cme": "14:30", "provisional": ["cme"]},
    "2027-05-31": {"name": "Memorial Day", "cme": "14:30", "ice": "13:30", "provisional": ["cme"]},
    "2027-06-18": {"name": "Juneteenth (observed)", "cme": "13:00", "ice": "13:30", "provisional": ["cme"]},
    "2027-07-05": {"name": "Independence Day (observed)", "cme": "13:00", "ice": "13:30", "provisional": ["cme"]},
    "2027-09-06": {"name": "Labor Day", "cme": "14:30", "ice": "13:30", "provisional": ["cme"]},
    "2027-11-25": {"name": "Thanksgiving", "cme": "14:30", "ice": "14:30", "provisional": ["cme"]},
    "2027-11-26": {"name": "Day after Thanksgiving", "cme": "14:45", "ice": "15:00", "provisional": ["cme"]},
    "2027-12-24": {"name": "Christmas (observed)", "ice": "08:00"},
    "2027-12-31": {"name": "New Year (in lieu)", "ice": "15:00"},
}


NFP_DATES = [   # US jobs report, 08:30 New York (BLS schedule; the feed's time wins)
    "2026-12-04", "2027-01-08", "2027-02-05", "2027-03-05", "2027-04-02", "2027-05-07",
    "2027-06-04", "2027-07-02", "2027-08-06", "2027-09-03", "2027-10-08", "2027-11-05",
    "2027-12-03",
]

# COT reports: 15:30 New York. 2026 is the official CFTC list; 2027 is a guess (expected).
COT_RELEASES = {"time": "15:30", "provisional_years": [2027], "dates": [
    "2026-01-05", "2026-01-09", "2026-01-16", "2026-01-23", "2026-01-30", "2026-02-06",
    "2026-02-13", "2026-02-20", "2026-02-27", "2026-03-06", "2026-03-13", "2026-03-20",
    "2026-03-27", "2026-04-03", "2026-04-10", "2026-04-17", "2026-04-24", "2026-05-01",
    "2026-05-08", "2026-05-15", "2026-05-22", "2026-05-29", "2026-06-05", "2026-06-12",
    "2026-06-22", "2026-06-26", "2026-07-06", "2026-07-10", "2026-07-17", "2026-07-24",
    "2026-07-31", "2026-08-07", "2026-08-14", "2026-08-21", "2026-08-28", "2026-09-04",
    "2026-09-11", "2026-09-18", "2026-09-25", "2026-10-02", "2026-10-09", "2026-10-16",
    "2026-10-23", "2026-10-30", "2026-11-06", "2026-11-16", "2026-11-20", "2026-11-30",
    "2026-12-04", "2026-12-11", "2026-12-18", "2026-12-28", "2027-01-04", "2027-01-08",
    "2027-01-15", "2027-01-22", "2027-01-29", "2027-02-05", "2027-02-12", "2027-02-19",
    "2027-02-26", "2027-03-05", "2027-03-12", "2027-03-19", "2027-03-26", "2027-04-02",
    "2027-04-09", "2027-04-16", "2027-04-23", "2027-04-30", "2027-05-07", "2027-05-14",
    "2027-05-21", "2027-05-28", "2027-06-04", "2027-06-11", "2027-06-21", "2027-06-25",
    "2027-07-02", "2027-07-09", "2027-07-16", "2027-07-23", "2027-07-30", "2027-08-06",
    "2027-08-13", "2027-08-20", "2027-08-27", "2027-09-03", "2027-09-10", "2027-09-17",
    "2027-09-24", "2027-10-01", "2027-10-08", "2027-10-15", "2027-10-22", "2027-10-29",
    "2027-11-05", "2027-11-15", "2027-11-19", "2027-11-29", "2027-12-03", "2027-12-10",
    "2027-12-17", "2027-12-27", "2028-01-03",
]}

# Central bank decision days. ECB and SNB times are London times (Frankfurt and Zurich
# are London + 1 h all year). Minutes dates come from the 3-week rule: provisional.
CB_MEETINGS = {
    "fed": {"currency": "USD", "city": "ny", "time": "14:00", "end": "14:30", "title": "Fed rate decision (FOMC)",
        "provisional": False, "dates": [
            "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16",
            "2026-10-28", "2026-12-09", "2027-01-27", "2027-03-17", "2027-04-28", "2027-06-09",
            "2027-07-28", "2027-09-15", "2027-10-27", "2027-12-08",
        ]},
    "fed_minutes": {"currency": "USD", "city": "ny", "time": "14:00", "end": None, "title": "Fed minutes",
        "provisional": True, "dates": [
            "2026-11-18", "2026-12-30", "2027-02-17", "2027-04-07", "2027-05-19", "2027-06-30",
            "2027-08-18", "2027-10-06", "2027-11-17", "2027-12-29",
        ]},
    "ecb": {"currency": "EUR", "city": "london", "time": "13:15", "end": "13:45", "title": "ECB rate decision",
        "provisional": False, "dates": [
            "2026-10-29", "2026-12-17", "2027-02-04", "2027-03-18", "2027-04-29", "2027-06-10",
            "2027-07-22", "2027-09-09", "2027-10-28", "2027-12-16",
        ]},
    "boe": {"currency": "GBP", "city": "london", "time": "12:00", "end": None, "title": "BoE rate decision",
        "provisional": False, "dates": [
            "2026-02-05", "2026-03-19", "2026-04-30", "2026-06-18", "2026-07-30", "2026-09-17",
            "2026-11-05", "2026-12-17", "2027-02-04", "2027-03-18", "2027-04-29", "2027-06-17",
            "2027-07-29", "2027-09-16", "2027-11-04", "2027-12-16",
        ]},
    "boj": {"currency": "JPY", "city": "tokyo", "time": "11:30", "end": "13:30", "title": "BoJ rate decision",
        "provisional": False, "dates": [
            "2026-01-23", "2026-03-19", "2026-04-28", "2026-06-16", "2026-07-31", "2026-09-18",
            "2026-10-30", "2026-12-18", "2027-01-22", "2027-03-18", "2027-04-28", "2027-06-11",
            "2027-07-22", "2027-09-22", "2027-10-29", "2027-12-17",
        ]},
    "snb": {"currency": "CHF", "city": "london", "time": "08:30", "end": "09:00", "title": "SNB rate decision",
        "provisional": False, "dates": [
            "2026-12-10", "2027-03-18", "2027-06-24", "2027-09-23", "2027-12-16",
        ]},
    "boc": {"currency": "CAD", "city": "ny", "time": "09:45", "end": None, "title": "BoC rate decision",
        "provisional": False, "dates": [
            "2026-12-09", "2027-01-27", "2027-03-03", "2027-04-28", "2027-06-02", "2027-07-21",
            "2027-09-08", "2027-10-27", "2027-12-08",
        ]},
}

# EIA weekly reports (10:30 New York). EIA_MOVES[report][normal date] =
# (moved "YYYY-MM-DD HH:MM" New York, why, provisional).
EIA_TIMES = {
    "crude": {"weekday": 2, "time": "10:30", "title": "Crude Oil Inventories", "symbols": ["USOIL", "UKOIL"]},
    "gas":   {"weekday": 3, "time": "10:30", "title": "Natural Gas Storage",   "symbols": ["NATGAS"]},
}
EIA_MOVES = {
    "crude": {
        "2026-11-11": ("2026-11-12 12:00", "Veterans Day", False),
        "2027-01-20": ("2027-01-21 12:00", "MLK Day (Monday)", True),
        "2027-02-17": ("2027-02-18 12:00", "Presidents' Day", True),
        "2027-06-02": ("2027-06-03 12:00", "Memorial Day", True),
        "2027-07-07": ("2027-07-08 12:00", "Independence Day", True),
        "2027-09-08": ("2027-09-09 12:00", "Labor Day", True),
        "2027-10-13": ("2027-10-14 12:00", "Columbus Day", True),
    },
    "gas": {
        "2026-11-12": ("2026-11-13 10:30", "Veterans Day", False),
        "2026-11-26": ("2026-11-25 12:00", "Thanksgiving", False),
        "2027-11-11": ("2027-11-10 12:00", "Veterans Day (Thursday)", True),
        "2027-11-25": ("2027-11-24 12:00", "Thanksgiving", True),
    },
}
ENERGY_KEYS = {"eia_crude": ("USOIL", "UKOIL"), "eia_gas": ("NATGAS",)}
# LBMA auction times (London time, UK business days); for key_times only.
LBMA_TIMES = {"gold": ["10:30", "15:00"], "silver": ["12:00"], "platinum": ["09:45", "14:00"],
              "palladium": ["09:45", "14:00"]}


def make_event(title, country, at, kind, impact, end=None, symbols=None, provisional=False,
               source="built-in", all_day=False, forecast=None, previous=None):
    """One event in the economic calendar shape (part 03 9.5)."""
    return {"title": title, "country": country, "at": at, "end": end, "kind": kind,
            "impact": impact, "forecast": forecast, "previous": previous, "source": source,
            "symbols": symbols, "all_day": all_day, "provisional": provisional}


def builtin_events(from_utc, to_utc, cfg):
    """Central bank decisions, US jobs reports, EIA oil and gas reports and COT releases
    from the tables, with from_utc <= at < to_utc, sorted by time."""
    out = []
    last = datetime.date.fromisoformat(TABLES_END)
    keep = lambda d, at: d <= last and from_utc <= at < to_utc
    for bank in CB_MEETINGS.values():
        for iso in bank["dates"]:
            d = datetime.date.fromisoformat(iso)
            at = city_to_utc(bank["city"], at_time(d, bank["time"]))
            if keep(d, at):
                end = city_to_utc(bank["city"], at_time(d, bank["end"])) if bank["end"] else None
                out.append(make_event(bank["title"], bank["currency"], at, "cb", "High", end=end,
                                      provisional=bank["provisional"]))
    for iso in NFP_DATES:
        d = datetime.date.fromisoformat(iso)
        at = ny_to_utc(at_time(d, "08:30"))
        if keep(d, at):
            out.append(make_event("US jobs report (NFP)", "USD", at, "nfp", "High"))
    for iso in COT_RELEASES["dates"]:
        d = datetime.date.fromisoformat(iso)
        at = ny_to_utc(at_time(d, COT_RELEASES["time"]))
        if keep(d, at):
            out.append(make_event("COT report (CFTC)", "USD", at, "cot", "Low",
                                  symbols=list((cfg or {}).get("instruments") or {}),
                                  provisional=d.year in COT_RELEASES["provisional_years"]))
    d = to_ny(from_utc).date() - datetime.timedelta(days=2)
    stop = to_ny(to_utc).date() + datetime.timedelta(days=2)
    while d <= stop:
        for report, row in sorted(EIA_TIMES.items()):
            if d.weekday() != row["weekday"]:
                continue
            moved = EIA_MOVES[report].get(d.isoformat())
            if moved:
                when, prov = datetime.datetime.strptime(moved[0], "%Y-%m-%d %H:%M"), moved[2]
            elif "cme" in HOLIDAYS.get(d.isoformat(), {}).get("closed", []):
                continue
            else:
                when, prov = at_time(d, row["time"]), False
            at = ny_to_utc(when)
            if keep(d, at):
                out.append(make_event(row["title"], "USD", at, "eia_" + report, "Low",
                                      symbols=list(row["symbols"]), provisional=prov))
        d += datetime.timedelta(days=1)
    return sorted(out, key=lambda e: e["at"])


def key_times(fx_date, cfg):
    """The fixed daily times of an FX day, as sorted (utc, label): US data times, the
    LBMA gold prices, the London 4 pm fix, the CME break and the rollover."""
    d = parse_fx_date(fx_date)
    out = [(ny_to_utc(at_time(d, "08:30")), "US data time"),
           (ny_to_utc(at_time(d, "10:00")), "US data time"),
           (london_to_utc(at_time(d, "16:00")), "London 4 pm FX fix"),
           (ny_to_utc(at_time(d, "17:00")), "Gold and oil daily break"),
           (ny_to_utc(at_time(d, "17:00")), "Rollover (swap is booked)")]
    row = HOLIDAYS.get(d.isoformat(), {})
    uk_holiday = "fx" in row.get("thin", []) and "(UK" in row.get("name", "")
    if not uk_holiday and not full_fx_holiday(d, cfg):
        out += [(london_to_utc(at_time(d, t)), "LBMA gold price") for t in LBMA_TIMES["gold"]]
    return sorted(out, key=lambda x: x[0])


# ---- market hours (part 03 section 6)

HOURS = {
    # broker trading hours (instrument column "hours")
    "fx":        {"group": "fx",  "open": ("ny", "17:05", -1),     "monday_open": ("ny", "17:05", -1)},
    "comex":     {"group": "cme", "open": ("ny", "18:00", -1),     "monday_open": ("ny", "18:00", -1)},
    "nymex":     {"group": "cme", "open": ("ny", "18:00", -1),     "monday_open": ("ny", "18:00", -1)},
    "ice_brent": {"group": "ice", "open": ("london", "01:00", 0),  "monday_open": ("london", "23:00", -1)},
    # Yahoo data hours (instrument column "data_hours")
    "yahoo_fx":  {"group": "fx",  "open": ("ny", "17:00", -1),     "monday_open": ("london", "00:00", 0)},
    "cme":       {"group": "cme", "open": ("ny", "18:00", -1),     "monday_open": ("ny", "18:00", -1)},
}
BROKER_TEMPLATES = ("fx", "comex", "nymex", "ice_brent")
DATA_TEMPLATES = ("yahoo_fx", "cme")
MARKET_WORDS = {"fx": "FX", "cme": "Gold and oil", "ice": "Brent"}


def hours_template(name):
    """The HOURS row of a template name; an unknown name: ValueError."""
    if name not in HOURS:
        raise ValueError('Unknown hours template "%s". Use one of: fx, comex, nymex, ice_brent, yahoo_fx, cme.' % name)
    return HOURS[name]


def group_closed(fx_date, group, cfg):
    """True when the whole FX day is shut for a holiday group (config "holidays" shut
    every group; HOLIDAYS shuts the groups in its "closed" list)."""
    iso = parse_fx_date(fx_date, weekend_ok=True).isoformat()
    if iso in ((cfg or {}).get("holidays") or {}):
        return True
    return group in HOLIDAYS.get(iso, {}).get("closed", [])


def early_close_utc(fx_date, group, cfg):
    """The early close of a group on that date as naive UTC (config "early_closes" for all
    groups, EARLY_CLOSES per group; the earlier wins), or None."""
    d = parse_fx_date(fx_date, weekend_ok=True)
    times = []
    mine = ((cfg or {}).get("early_closes") or {}).get(d.isoformat())
    if mine:
        times.append(mine)
    table = EARLY_CLOSES.get(d.isoformat(), {}).get(group)
    if table:
        times.append(table)
    if not times:
        return None
    return min(ny_to_utc(at_time(d, t)) for t in times)


def yahoo_no_data_cuts(fx_date):
    """The (start_utc, end_utc) cuts where Yahoo has no futures bars, for an FX day: each
    New York date of fx_date - 1 and fx_date that is a US holiday for "cme" (closed or
    no_data); a Monday also loses the Sunday evening from 18:00."""
    d = parse_fx_date(fx_date, weekend_ok=True)
    day = datetime.timedelta(days=1)
    cuts = []
    for c in (d - day, d):
        row = HOLIDAYS.get(c.isoformat(), {})
        if "cme" in row.get("closed", []) or "cme" in row.get("no_data", []):
            start = ny_to_utc(at_time(c, "00:00"))
            if c.weekday() == 0:
                start = ny_to_utc(at_time(c - day, "18:00"))
            cuts.append((start, ny_to_utc(at_time(c + day, "00:00"))))
    return cuts


def open_spans(template, fx_date, cfg, tables=True):
    """The open time of a template on one FX day: [] or [(open_utc, close_utc)].
    tables=False skips holidays, early closes and the Yahoo no-data cut."""
    row = hours_template(template)
    d = parse_fx_date(fx_date)
    group = row["group"]
    if tables and group_closed(d, group, cfg):
        return []
    city, hhmm, shift = row["monday_open"] if d.weekday() == 0 else row["open"]
    start = city_to_utc(city, at_time(d + datetime.timedelta(days=shift), hhmm))
    close = ny_to_utc(at_time(d, "17:00"))
    if tables:
        early = early_close_utc(d, group, cfg)
        if early is not None:
            close = min(close, early)
        if template in DATA_TEMPLATES and group == "cme":
            for a, b in yahoo_no_data_cuts(d):
                if a <= start:
                    start = max(start, b)
                elif a < close:
                    close = a
    return [(start, close)] if start < close else []


def day_close_utc(inst, fx_date, cfg):
    """When the instrument's broker market closes on that FX day (the early close on a
    short day), as naive UTC; None when it is shut all that day."""
    spans = open_spans(inst["hours"], fx_date, cfg)
    return spans[-1][1] if spans else None


def holiday_note(date, inst, cfg):
    """One plain line about a holiday, a thin day or an early close for the instrument's
    market on that FX date, or None on a normal day."""
    d = parse_fx_date(date, weekend_ok=True)
    iso = d.isoformat()
    group = hours_template(inst["hours"])["group"]
    word = MARKET_WORDS[group]
    day = short_date(d)
    mine = ((cfg or {}).get("holidays") or {})
    if iso in mine:
        return "%s: your broker is shut (%s)." % (day, mine[iso])
    row = HOLIDAYS.get(iso)
    early = EARLY_CLOSES.get(iso, {})
    sure = " These hours are expected, not confirmed. Check your broker's notice."
    if row and group in row.get("closed", []):
        text, prov = "%s: %s. %s is shut all day." % (day, row["name"], word), row.get("provisional", [])
    else:
        close = early_close_utc(d, group, cfg)
        if close is not None:
            name = early.get("name") if early.get(group) else None
            text = "%s: %s. %s stops early, at %s (%s)." % (
                day, name or "Early close (your config)", word, ny_label(close), pkt_label(close))
            prov = early.get("provisional", []) if name else []
        elif row and group in row.get("thin", []):
            text, prov = "%s: %s. Open, but few traders. Spreads can be wide." % (day, row["name"]), row.get("provisional", [])
        elif row:
            text, prov = "%s: %s. Markets are open as usual." % (day, row["name"]), row.get("provisional", [])
        elif iso > TABLES_END:
            return "This tool's holiday list ends on %s. Check your broker's holiday notice." % TABLES_END
        elif iso < TABLES_START:
            return "This tool's holiday list starts on %s." % TABLES_START
        else:
            return None
    return text + (sure if group in prov else "")


def market_state(inst, utc, cfg, which="hours"):
    """Is the instrument's market open now? {state: open, break, closed, weekend or
    holiday; next_open, next_close (naive UTC or None), note, template}.
    which="data_hours" asks about Yahoo's data hours instead of the broker's."""
    template = inst[which]
    row = hours_template(template)
    group = row["group"]
    base = to_ny(utc).date()
    spans = []
    for k in range(-1, 23):
        c = base + datetime.timedelta(days=k)
        if c.weekday() < 5:
            spans += open_spans(template, c, cfg)
    current = [s for s in spans if s[0] <= utc < s[1]]
    later = [s for s in spans if s[0] > utc]
    next_open = later[0][0] if later else None
    next_close = current[0][1] if current else (later[0][1] if later else None)
    out = {"state": "closed", "next_open": next_open, "next_close": next_close, "note": "", "template": template}
    d = fx_day(utc, "17:00 New York")
    if d is None:
        out.update(state="weekend", note="Closed for the weekend.")
        return out
    note = holiday_note(d, inst, cfg)
    if group_closed(d, group, cfg):
        out.update(state="holiday", note=note or "")
        return out
    if template in DATA_TEMPLATES and group == "cme" and any(a <= utc < b for a, b in yahoo_no_data_cuts(d)):
        out.update(state="holiday", note="No Yahoo futures prices on US holidays.")
        return out
    if current:
        out.update(state="open", note=note or "")
        return out
    early = early_close_utc(d, group, cfg)
    if early is not None and utc >= early:
        out.update(state="holiday", note=note or "")
        return out
    today = open_spans(template, d, cfg, tables=False)
    if today and utc < today[0][0]:
        if parse_fx_date(d).weekday() == 0:
            if template == "yahoo_fx":
                out.update(state="closed", note="Yahoo has no FX prices before Monday 00:00 London.")
            else:
                out.update(state="weekend", note="Closed for the weekend.")
            return out
        breaks = {"fx": "Short pause at the 5:00 PM New York rollover. Spreads are wide.",
                  "ice": "Brent is shut from 5:00 PM New York to 1:00 AM London.",
                  "cme": "Daily break from 5:00 PM to 6:00 PM New York."}
        out.update(state="break", note=breaks[group])
        return out
    out["note"] = note or ""
    return out


def is_open(inst, utc, cfg, which="hours"):
    """True when market_state says "open"."""
    return market_state(inst, utc, cfg, which)["state"] == "open"


def entry_cutoff(inst, fx_date, now, cfg):
    """The last moment for a new day trade (05 4.1): day.no_entries_after on the FX day,
    or 30 minutes before an early close when that comes first. now is not used (check)."""
    day_end = (cfg or {}).get("day_end", "17:00 New York")
    cut = pkt_time_on_fx_day(cfg["day"]["no_entries_after"], fx_date, day_end)
    close = day_close_utc(inst, fx_date, cfg)
    if close is None:
        return cut
    return min(cut, close - datetime.timedelta(minutes=30))


def last_done_fx_day(inst, now, cfg):
    """The newest finished FX day (its end at or before now) on which Yahoo has data
    hours for the instrument; looks back at most 10 FX days; None when none."""
    day_end = (cfg or {}).get("day_end", "17:00 New York")
    d = parse_fx_date(last_fx_day(now, cfg))
    seen = 0
    while seen < 10:
        if d.weekday() < 5:
            seen += 1
            if fx_day_bounds(d, day_end)[1] <= now and open_spans(inst["data_hours"], d, cfg):
                return d.isoformat()
        d -= datetime.timedelta(days=1)
    return None


# ---- economic calendar (part 03 section 9)

CALENDAR_MIN_REFRESH_S = 3600   # at most one calendar download an hour, also after a failure
CALENDAR_AHEAD_DAYS = 35        # built-in events this far ahead (a 20-FX-day swing hold)
CALENDAR_TIMEOUT = 20           # seconds; one try, never a retry loop
# Feed titles (lower case, "contains") that match a built-in event: the bank keys of
# CB_MEETINGS, then the other kinds. parse_ff sets "kind" from this list too.
FEED_TITLES = {
    "fed": ("federal funds rate", "fomc statement"), "fed_minutes": ("fomc meeting minutes",),
    "ecb": ("main refinancing rate",), "boe": ("official bank rate",), "boj": ("boj policy rate",),
    "snb": ("snb policy rate",), "boc": ("overnight rate",), "nfp": ("non-farm employment change",),
    "eia_crude": ("crude oil inventories",), "eia_gas": ("natural gas storage",),
}


def calendar_week(utc):
    """The New York date of the Sunday on or before this moment's New York date, as
    "YYYY-MM-DD" (the calendar file's week)."""
    n = to_ny(utc).date()
    return (n - datetime.timedelta(days=(n.weekday() + 1) % 7)).isoformat()


def event_label(event):
    """An event's name for people: "Main Refinancing Rate (EUR)"."""
    return "%s (%s)" % (event.get("title") or "", event.get("country") or "")


def feed_kind(title):
    """The FEED_TITLES key a feed title matches ("fed", "eia_crude" ...), or None."""
    low = (title or "").lower()
    for key, words in FEED_TITLES.items():
        if any(w in low for w in words):
            return key
    return None


def parse_ff(doc):
    """Calendar feed rows (a calendar file or a raw list) -> events with UTC times,
    no duplicates, sorted by time. Bad rows are dropped."""
    rows = doc.get("events") if isinstance(doc, dict) else doc
    out, seen = [], set()
    for row in rows or []:
        if not isinstance(row, dict) or not isinstance(row.get("date"), str):
            continue
        text = lambda k: str(row.get(k) or "").strip()
        date, impact = text("date"), text("impact")
        at, end, all_day = parse_utc(date), None, False
        if (at is None or impact == "Holiday" or len(date) == 10) and re.match(r"^\d{4}-\d{2}-\d{2}", date):
            try:
                d = datetime.date.fromisoformat(date[:10])
            except ValueError:
                continue
            at = ny_to_utc(at_time(d, "00:00"))
            end = ny_to_utc(at_time(d + datetime.timedelta(days=1), "00:00"))
            all_day = True
        if at is None:
            continue
        key = feed_kind(text("title"))
        kind = "cb" if key in CB_MEETINGS else key
        ev = make_event(text("title"), text("country"), at, kind, impact, end=end, source="forexfactory",
                        all_day=all_day, forecast=text("forecast") or None, previous=text("previous") or None)
        k = (ev["title"], ev["country"], ev["at"])
        if k not in seen:
            seen.add(k)
            out.append(ev)
    return sorted(out, key=lambda e: e["at"])


def event_symbols(event, cfg):
    """The instrument keys an event touches, in table order: its own "symbols" (or the
    EIA keys by kind), every key for "All", else each pair with the country as a leg."""
    keys = list((cfg or {}).get("instruments") or {})
    own = event.get("symbols") or ENERGY_KEYS.get(event.get("kind"))
    if own:
        return [k for k in keys if k in own]
    country = event.get("country")
    if country == "All":
        return keys
    rows = cfg["instruments"]
    return [k for k in keys if country in (rows[k].get("base"), rows[k].get("quote"))]


def event_impact(event, symbol):
    """The feed's impact, raised to "High" for EIA oil reports on oil and EIA gas
    reports on gas."""
    if symbol in ENERGY_KEYS.get(event.get("kind"), ()):
        return "High"
    return event.get("impact")


def quiet_windows(events, symbol, cfg):
    """(start_utc, end_utc, event) around each timed event of a counted impact that
    touches symbol: news.quiet_before_min before it to news.quiet_after_min after it."""
    news = cfg["news"]
    before = datetime.timedelta(minutes=news["quiet_before_min"])
    after = datetime.timedelta(minutes=news["quiet_after_min"])
    out = []
    for e in events:
        syms = e.get("symbols")
        if syms is None:
            syms = event_symbols(e, cfg)
        if symbol not in syms or e.get("all_day") or event_impact(e, symbol) not in news["impacts"]:
            continue
        out.append((e["at"] - before, (e.get("end") or e["at"]) + after, e))
    return sorted(out, key=lambda w: w[0])


def in_quiet_window(events, symbol, utc, cfg):
    """The first event (by time) whose quiet window holds utc, or None."""
    hits = [w for w in quiet_windows(events, symbol, cfg) if w[0] <= utc < w[1]]
    return min(hits, key=lambda w: w[2]["at"])[2] if hits else None


def next_events(events, symbols, utc, n, impacts=None):
    """The first n events (by time) not over yet that touch any of symbols (None = all),
    and with impacts given, whose impact for one of those symbols is in impacts."""
    out = []
    for e in sorted(events, key=lambda x: x["at"]):
        if (e.get("end") or e["at"]) < utc:
            continue
        own = e.get("symbols") or []
        touched = list(own) if symbols is None else [s for s in symbols if s in own]
        if symbols is not None and not touched:
            continue
        if impacts is not None:
            if touched:
                if not any(event_impact(e, s) in impacts for s in touched):
                    continue
            elif e.get("impact") not in impacts:
                continue
        out.append(e)
        if len(out) >= n:
            break
    return out


def calendar_get(url, headers, timeout=CALENDAR_TIMEOUT):
    """One GET of the calendar feed, never retried and never raising:
    (status or None on a network error, body bytes, response headers dict)."""
    req = urllib.request.Request(url, headers=dict(headers, **{"User-Agent": UA}))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers.items())
    except urllib.error.HTTPError as exc:
        return exc.code, b"", dict(exc.headers.items()) if exc.headers else {}
    except Exception:
        return None, b"", {}


def merge_builtin(feed, builtin, feed_ok):
    """The built-in events minus those the feed (of an ok file) already has: the same
    country, within 3 hours, and a matching title. COT events always stay."""
    if not feed_ok:
        return list(builtin)
    titles = dict((b["title"], k) for k, b in CB_MEETINGS.items())
    out = []
    for b in builtin:
        key = titles.get(b["title"]) if b["kind"] == "cb" else b["kind"]
        words = FEED_TITLES.get(key, ())
        dup = b["kind"] != "cot" and any(
            f["country"] == b["country"] and abs(f["at"] - b["at"]) <= datetime.timedelta(hours=3)
            and any(w in f["title"].lower() for w in words) for f in feed)
        if not dup:
            out.append(b)
    return out


def calendar_good_rows(status, body):
    """(rows, None) for a good feed answer, else (None, error): "http <code>",
    "not json", "not a list", "empty" or "network"."""
    if status is None:
        return None, "network"
    if status != 200:
        return None, "http %d" % status
    try:
        rows = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, "not json"
    if not isinstance(rows, list):
        return None, "not a list"
    if not any(isinstance(r, dict) and all(k in r for k in ("title", "country", "date")) for r in rows):
        return None, "empty"
    return rows, None


def calendar_refresh(path, doc, week, now, url):
    """Download this week's feed once into path (part 03 9.3 steps 3 to 6); returns the
    week's file doc. Never raises on a bad answer: it records the error instead."""
    old = doc or {"fetched_at": None, "tried_at": None, "error": None, "last_modified": None,
                  "etag": None, "url": url, "week": week, "ok": False, "events": []}
    head = {}
    if doc and doc.get("etag"):
        head["If-None-Match"] = doc["etag"]
    if doc and doc.get("last_modified"):
        head["If-Modified-Since"] = doc["last_modified"]
    status, body, rh = calendar_get(url, head)
    stamp = utc_iso(now)
    new = dict(old, tried_at=stamp, url=url)
    if status == 304 and doc:
        new.update(fetched_at=stamp, ok=True, error=None)
        save_json_atomic(path, new)
        return new
    rows, error = calendar_good_rows(status, body)
    if rows is None:
        new.update(ok=False, error=error if status != 304 else "http 304")
        save_json_atomic(path, new)
        return new
    times = sorted(t for t in (parse_utc(r.get("date")) for r in rows if isinstance(r, dict)) if t)
    their = calendar_week(times[len(times) // 2]) if times else week
    low = dict((k.lower(), v) for k, v in rh.items())
    good = {"fetched_at": stamp, "tried_at": stamp, "error": None, "last_modified": low.get("last-modified"),
            "etag": low.get("etag"), "url": url, "week": their, "ok": True, "events": rows}
    if their != week:
        save_json_atomic(os.path.join(os.path.dirname(path), their + ".json"), good)
        new.update(ok=False, error="old week")
        save_json_atomic(path, new)
        return new
    save_json_atomic(path, good)
    return good


def calendar_file_doc(path):
    """The saved calendar file, or None when it is missing or broken (check: a broken
    file is read as missing, so the next download replaces it)."""
    try:
        doc = load_json(path, None)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def load_calendar(data_dir, now, cfg, offline):
    """This week's events: the saved feed file (downloaded at most once an hour, never
    when offline) plus the built-in tables up to CALENDAR_AHEAD_DAYS ahead, each with
    "symbols", sorted by time."""
    week = calendar_week(now)
    path = os.path.join(data_dir, "calendar", week + ".json")
    doc = calendar_file_doc(path)
    url = cfg.get("calendar_url", "")
    if not offline and url:
        last = parse_utc((doc or {}).get("tried_at") or (doc or {}).get("fetched_at"))
        if doc is None or last is None or (now - last).total_seconds() >= CALENDAR_MIN_REFRESH_S:
            doc = calendar_refresh(path, doc, week, now, url)
    feed = parse_ff(doc) if (doc and url) else []
    start = ny_to_utc(at_time(datetime.date.fromisoformat(week), "00:00"))
    built = builtin_events(start, now + datetime.timedelta(days=CALENDAR_AHEAD_DAYS), cfg)
    events = feed + merge_builtin(feed, built, bool(doc and doc.get("ok")))
    for e in events:
        e["symbols"] = event_symbols(e, cfg)
    return sorted(events, key=lambda e: e["at"])


def calendar_info(data_dir, now):
    """The status of this week's calendar file: {week, ok, fetched_at, tried_at,
    age_min, error, events}; None values when there is no file. events is a count."""
    week = calendar_week(now)
    doc = calendar_file_doc(os.path.join(data_dir, "calendar", week + ".json")) or {}
    fetched = parse_utc(doc.get("fetched_at"))
    return {"week": week, "ok": doc.get("ok"), "fetched_at": doc.get("fetched_at"),
            "tried_at": doc.get("tried_at"), "error": doc.get("error"),
            "age_min": int((now - fetched).total_seconds() // 60) if fetched else None,
            "events": len(doc["events"]) if isinstance(doc.get("events"), list) else None}


# ---------------------------------------------------------------- yahoo client, cache, bars, rolls, broker prices
# group: yahoo client, cache, bars, rolls, broker prices

# ---- yahoo client (part 02 section 1)

UA = "Mozilla/5.0 (compatible; fx-trader-skill/1.0)"
YAHOO_HOSTS = ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com")
PACE_SECONDS = 2.0     # at least this long between any two Yahoo requests (both hosts)
TIMEOUT = 25           # socket timeout in seconds
SPARK_MAX = 20         # spark takes at most 20 symbols (21 gives HTTP 400)
LOOKBACK = {"5m": 60, "1h": 730, "1d": 3650}           # days Yahoo keeps (1d: we cap at 10 years)
FIRST_RANGE = {"5m": "60d", "1h": "730d", "1d": "2y"}  # the range of a first fill
MIN_REFRESH_S = {"5m": 60, "1h": 600, "1d": 21600}     # skip the network if the newest fetch is younger
STEP_S = {"5m": 300, "1h": 3600, "1d": 86400}          # bar length in seconds
SLEEP = time.sleep     # tests replace it
RETRY_WAIT_S = {"broken": 2, "busy": 5, "down": 3}     # wait before the one retry on the other host
_last_request = None   # time.monotonic() of the last Yahoo request of this run
_last_host = "query1"  # the host of the last answer (for the fetch record)


class NoData(ValueError):
    """Yahoo has no data for that symbol (a 404, or an answer with no result)."""


def chart_url(host, symbol, interval, range_=None, period1=None, period2=None):
    """The v8 chart URL for one symbol: a range, or a period1 and period2 (Unix seconds)."""
    if interval not in ("5m", "1h", "1d"):
        raise ValueError("Interval %s is not used by this pack" % interval)
    by_range = range_ is not None
    by_period = period1 is not None and period2 is not None
    if by_range == by_period:
        raise ValueError("chart_url needs a range or a period1 and period2")
    q = "interval=%s&" % interval
    if by_range:
        q += "range=%s" % range_
    else:
        q += "period1=%d&period2=%d" % (int(period1), int(period2))
    return host + "/v8/finance/chart/" + urllib.parse.quote(symbol, safe="") + "?" + q + "&includePrePost=false"


def _url_symbol(path):
    """The symbol a Yahoo URL asks for (the chart path, or the spark symbols list)."""
    parts = urllib.parse.urlsplit(path)
    if "/v8/finance/chart/" in parts.path:
        return urllib.parse.unquote(parts.path.rsplit("/", 1)[-1])
    q = urllib.parse.parse_qs(parts.query)
    return ",".join(q.get("symbols", [""]))


def _url_interval(path):
    """The interval a Yahoo URL asks for, or "" when it has none."""
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
    return q.get("interval", [""])[0]


def is_contract_symbol(symbol):
    """True for a single futures contract like GCZ26.CMX or CLF27.NYM (not GC=F)."""
    return re.match(r"^[A-Z]{1,3}[FGHJKMNQUVXZ]\d{2}\.(CMX|NYM)$", str(symbol)) is not None


def _yahoo_error(status, symbol, interval, description=""):
    """The plain error for a Yahoo HTTP status (part 02 1.3), or None for 200."""
    if status == 404:
        if is_contract_symbol(symbol):
            return NoData("Yahoo has no data for %s. Old contracts vanish from Yahoo." % symbol)
        return NoData("Yahoo has no data for %s. Check the yahoo symbol in your config." % symbol)
    if status == 422:
        return ValueError("Yahoo keeps 5-minute bars for 60 days and 1-hour bars for 730 days. "
                          "This asked for older data (%s, %s)." % (symbol, interval))
    if status == 401:
        return ValueError("Yahoo refused this kind of request.")
    if status == 429:
        return ValueError("Yahoo is busy (too many requests). Wait a few minutes, then try again. Or use --offline.")
    if status == "broken":
        return ValueError("Yahoo sent a broken answer for %s. Try again in a minute." % symbol)
    if status is None or 500 <= status <= 599:
        return ValueError("Yahoo did not answer for %s. Try again later, or use --offline." % symbol)
    if status == 200:
        return None
    return ValueError("Yahoo refused the request for %s: %s" % (symbol, description or "HTTP %s" % status))


def _error_description(body):
    """chart.error.description (or spark.error.description) from an error body, or ""."""
    try:
        doc = json.loads(body.decode("utf-8", "replace"))
        for key in ("chart", "spark", "finance"):
            err = (doc.get(key) or {}).get("error") or {}
            if err.get("description"):
                return str(err["description"])
    except Exception:
        pass
    return ""


def _pace():
    """Wait until PACE_SECONDS have passed since the last Yahoo request, then mark now."""
    global _last_request
    if _last_request is not None:
        wait = PACE_SECONDS - (time.monotonic() - _last_request)
        if wait > 0:
            SLEEP(wait)
    _last_request = time.monotonic()


def _gunzip(body, headers):
    """body unzipped when the headers say Content-Encoding gzip. A gzip body that was
    cut short gives b"" (so the caller sees a broken answer, not no answer)."""
    if ((headers.get("Content-Encoding") if headers else "") or "").lower() != "gzip":
        return body
    try:
        return gzip.decompress(body)
    except Exception:
        return b""


def _one_get(url, timeout):
    """One raw GET: (status or None when nothing came back, body bytes)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json",
                                               "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, raw, headers = getattr(resp, "status", 200), resp.read(), resp.headers
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read() or b""
        except Exception:
            body = b""
        return exc.code, _gunzip(body, exc.headers)
    except Exception:
        return None, b""
    return status, _gunzip(raw, headers)


def yahoo_get(path, timeout=TIMEOUT):
    """One paced GET on query1 with the retry rules of part 02 1.3 (one retry on query2
    for a broken answer, a 429, a 5xx or no answer); returns the body or raises."""
    global _last_host
    symbol, interval = _url_symbol(path), _url_interval(path)
    url = path
    for attempt in (0, 1):
        _pace()
        status, body = _one_get(url, timeout)
        _last_host = "query2" if url.startswith(YAHOO_HOSTS[1]) else "query1"
        if status == 200:
            try:
                json.loads(body.decode("utf-8"))
                return body
            except Exception:
                kind = "broken"
        elif status == 429:
            kind = "busy"
        elif status is None or 500 <= status <= 599:
            kind = "down"
        else:
            raise _yahoo_error(status, symbol, interval, _error_description(body))
        if attempt == 1:
            raise _yahoo_error(None if kind == "down" else (429 if kind == "busy" else "broken"),
                               symbol, interval)
        SLEEP(RETRY_WAIT_S[kind])
        if url.startswith(YAHOO_HOSTS[0]):
            url = YAHOO_HOSTS[1] + url[len(YAHOO_HOSTS[0]):]


_ERROR_CODES = {"Not Found": 404, "Unprocessable Entity": 422, "Bad Request": 400,
                "Unauthorized": 401, "Too Many Requests": 429}


def fetch_chart(symbol, interval, range_=None, period1=None, period2=None):
    """GET one chart answer and return chart.result[0] (meta, maybe timestamp, indicators).
    An error in the body raises the part 02 1.3 message; no result raises NoData."""
    body = yahoo_get(chart_url(YAHOO_HOSTS[0], symbol, interval, range_, period1, period2))
    doc = json.loads(body.decode("utf-8"))
    chart = doc.get("chart") or {}
    err = chart.get("error")
    if err:
        code = _ERROR_CODES.get(str(err.get("code")), 400)
        raise _yahoo_error(code, symbol, interval, str(err.get("description") or ""))
    result = chart.get("result")
    if not result:
        raise NoData("Yahoo sent no data for %s." % symbol)
    return result[0]


def parse_chart(result, symbol=""):
    """A chart result -> (meta, rows); rows are [t, o, h, l, c, v], raw, nulls kept.
    The quote lists are read by name; a missing list counts as all nulls."""
    meta = result.get("meta") or {}
    ts = result.get("timestamp") or []
    q = ((result.get("indicators") or {}).get("quote") or [{}])[0] or {}
    cols = []
    for name in ("open", "high", "low", "close", "volume"):
        col = q.get(name)
        if col is None:
            col = [None] * len(ts)
        if len(col) != len(ts):
            raise ValueError("Yahoo sent a broken chart for %s (lists of different length)."
                             % (symbol or meta.get("symbol") or "?"))
        cols.append(col)
    rows = [[ts[i]] + [c[i] for c in cols] for i in range(len(ts))]
    return meta, rows


def spark(symbols):
    """Quick closes for a printout, never for levels: {asked symbol: {close, at, short_name}},
    in calls of at most SPARK_MAX symbols; unknown symbols are just missing."""
    out = {}
    symbols = list(symbols)
    for i in range(0, len(symbols), SPARK_MAX):
        part = symbols[i:i + SPARK_MAX]
        url = (YAHOO_HOSTS[0] + "/v7/finance/spark?symbols="
               + ",".join(urllib.parse.quote(s, safe="") for s in part) + "&range=1d&interval=5m")
        doc = json.loads(yahoo_get(url).decode("utf-8"))
        for item in ((doc.get("spark") or {}).get("result") or []):
            sym = item.get("symbol")
            resp = (item.get("response") or [None])[0]
            if sym not in part or not resp:
                continue
            meta, rows = parse_chart(resp, sym)
            closes = [r[4] for r in rows if r[4] is not None]
            at = meta.get("regularMarketTime")
            out[sym] = {"close": closes[-1] if closes else meta.get("regularMarketPrice"),
                        "at": ts_to_utc(at) if at is not None else None,
                        "short_name": meta.get("shortName")}
    return out


# ---- cache (part 02 section 2)

FETCH_LOG_MAX = 400    # fetch records kept per cache file (newest last)


def cache_file(data_dir, symbol, interval):
    """The cache path data_dir/yahoo/<file>/<interval>.json; <file> is the symbol with
    "=" and "." turned into "_" (GC=F -> GC_F). A strange symbol is refused."""
    if not re.match(r"^[A-Za-z0-9=.^-]+$", str(symbol)):
        raise ValueError("Bad Yahoo symbol: %s" % symbol)
    name = str(symbol).replace("=", "_").replace(".", "_")
    return os.path.join(data_dir, "yahoo", name, "%s.json" % interval)


def load_cache(data_dir, symbol, interval):
    """The cache doc of one symbol and interval; an empty doc when the file is missing."""
    path = cache_file(data_dir, symbol, interval)
    empty = {"symbol": symbol, "interval": interval, "bars": [], "fetches": []}
    if not os.path.exists(path):
        return empty
    try:
        with open(path, "rb") as fh:
            doc = json.loads(fh.read().decode("utf-8"))
        if not isinstance(doc, dict) or not isinstance(doc.get("bars"), list):
            raise ValueError(path)
    except (OSError, ValueError):
        raise ValueError("The price cache %s is broken. Delete it and run again with the network on." % path)
    doc.setdefault("fetches", [])
    return doc


def save_cache(path, doc):
    """Write a cache doc as compact JSON (path + ".tmp", then one move), making the folder."""
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(doc, separators=(",", ":")) + "\n")
        os.replace(tmp, path)
    except OSError as exc:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise ValueError("could not save %s: %s" % (path, exc.strerror or exc))


def last_fetch(doc):
    """The newest fetch record of a cache doc, or None."""
    fetches = (doc or {}).get("fetches") or []
    return fetches[-1] if fetches else None


def merge_rows(old, new, window, now, interval):
    """Old rows inside the new answer's window (first_t, last_t) go; the new rows come in
    (a new row wins on the same t); sorted by t; rows older than LOOKBACK + 30 days are cut."""
    keep = list(old)
    if window and window[0] is not None and window[1] is not None:
        keep = [r for r in keep if not (window[0] <= r[0] <= window[1])]
    by_t = {}
    for r in keep:
        by_t[r[0]] = list(r)
    for r in new:
        by_t[r[0]] = list(r)
    cut = utc_to_ts(now) - (LOOKBACK[interval] + 30) * 86400
    return [by_t[t] for t in sorted(by_t) if t > cut]


def stored_rows(rows, market_time):
    """The raw rows a cache stores: a non-null close, and not the live tick (the last row
    at market_time, or any row off the whole minute)."""
    out = []
    for i, r in enumerate(rows):
        if r[4] is None or r[0] % 60 != 0:
            continue
        if i == len(rows) - 1 and market_time is not None and r[0] == market_time:
            continue
        out.append(list(r))
    return out


def refresh(data_dir, symbol, interval, now, cfg, offline):
    """Bring one cache file up to date: no network when offline or when the newest fetch
    is younger than MIN_REFRESH_S; else a first fill (a range) or a small incremental
    fetch. Records the fetch and saves. A client ValueError is raised to the caller."""
    doc = load_cache(data_dir, symbol, interval)
    if offline:
        return doc
    last = last_fetch(doc)
    at = parse_utc((last or {}).get("at"))
    if at is not None and (now - at).total_seconds() < MIN_REFRESH_S[interval]:
        return doc
    step = STEP_S[interval]
    bars = doc["bars"]
    range_ = period1 = period2 = None
    old_edge = utc_to_ts(now) - (LOOKBACK[interval] - 5) * 86400
    if not bars or bars[-1][0] < old_edge:
        range_ = FIRST_RANGE[interval]
    else:
        period1 = bars[-1][0] - 2 * step
        period2 = utc_to_ts(now) + 60
    return fetch_into(data_dir, doc, symbol, interval, now, range_, period1, period2)


def fetch_into(data_dir, doc, symbol, interval, now, range_=None, period1=None, period2=None):
    """One chart fetch merged into a cache doc (live tick dropped, fetch recorded), saved."""
    result = fetch_chart(symbol, interval, range_=range_, period1=period1, period2=period2)
    meta, rows = parse_chart(result, symbol)
    kept = stored_rows(rows, meta.get("regularMarketTime"))
    window = (kept[0][0], kept[-1][0]) if kept else None
    doc["bars"] = merge_rows(doc["bars"], kept, window, now, interval)
    doc["fetches"].append({
        "at": utc_iso(now), "host": _last_host, "range": range_, "period1": period1,
        "period2": period2, "first": kept[0][0] if kept else None,
        "last": kept[-1][0] if kept else None, "rows": len(kept),
        "market_time": meta.get("regularMarketTime"), "price": meta.get("regularMarketPrice"),
        "short_name": meta.get("shortName"), "currency": meta.get("currency"),
        "tz": meta.get("exchangeTimezoneName"), "gmtoffset": meta.get("gmtoffset"),
        "previous_close": meta.get("previousClose")})
    doc["fetches"] = doc["fetches"][-FETCH_LOG_MAX:]
    save_cache(cache_file(data_dir, symbol, interval), doc)
    return doc


# ---- bars (part 02 sections 2.4, 2.5, 3, 4, 6 and 7)

DONE_MARGIN_MIN = 15          # a span's last bar is done this long after the close
OUTAGE_MIN_SLOTS = {"5m": 6, "1h": 3, "1d": 1}   # an outage misses this many steps of open time
COARSE = {"EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"}
COARSE_MIN_STOP_PIPS = 10
COARSE_MARGIN_PIPS = 2
STALE_MINUTES = {"spot_fx": 15, "futures": 30}
YAHOO_DECIMALS = {"GC": 1, "SI": 3, "PL": 1, "PA": 1, "HG": 4, "CL": 2, "BZ": 2, "NG": 3}
FUTURES_KIND = {"GC": "metal", "SI": "metal", "PL": "metal", "PA": "metal", "HG": "metal",
                "CL": "energy", "BZ": "energy", "NG": "energy"}


def symbol_root(symbol):
    """The futures root of a Yahoo symbol (GC=F and GCZ26.CMX -> "GC"), else None."""
    m = re.match(r"^([A-Z]{1,3})=F$", str(symbol)) or \
        re.match(r"^([A-Z]{1,3})[FGHJKMNQUVXZ]\d{2}\.(CMX|NYM)$", str(symbol))
    return m.group(1) if m else None


def yahoo_inst(symbol):
    """A small stand-in instrument row made from a Yahoo symbol alone (data hours, root,
    kind, quote, yahoo_to_broker 1.0), for callers that have no config row."""
    root = symbol_root(symbol)
    if root:
        return {"key": symbol, "yahoo": symbol, "kind": FUTURES_KIND.get(root, "energy"),
                "price_source": "futures", "hours": "comex", "data_hours": "cme",
                "quote": "USD", "digits": YAHOO_DECIMALS.get(root, 2), "yahoo_to_broker": 1.0,
                "futures": {"root": root}}
    pair = str(symbol).split("=")[0]
    return {"key": pair, "yahoo": symbol, "kind": "fx", "price_source": "spot_fx", "hours": "fx",
            "data_hours": "yahoo_fx", "base": pair[:3], "quote": pair[3:6],
            "digits": 3 if pair[3:6] == "JPY" else 5, "yahoo_to_broker": 1.0, "futures": None}


def inst_root(inst):
    """The futures root of an instrument row (inst["futures"]["root"]), else None."""
    fut = inst.get("futures") or {}
    return fut.get("root") or (symbol_root(inst.get("yahoo")) if inst.get("price_source") == "futures" else None)


def yahoo_decimals(inst):
    """The decimals Yahoo sends for the instrument's symbol: FX quoted in JPY 3, other
    FX 5; GC 1, SI 3, PL 1, PA 1, HG 4, CL 2, BZ 2, NG 3 (else the row's digits)."""
    root = inst_root(inst)
    if root in YAHOO_DECIMALS:
        return YAHOO_DECIMALS[root]
    if inst.get("kind") == "fx":
        return 3 if inst.get("quote") == "JPY" else 5
    return int(inst.get("digits", 5))


def _round_dec(x, places):
    """A float rounded half up to places decimals (through its shortest text)."""
    return float(Decimal(repr(float(x))).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def clean_rows(rows, meta, interval, inst):
    """Raw rows -> bars {start, open, high, low, close, volume}: drops the live tick, rows
    with a null price and (FX and metals) prices of 0 or less; rounds to yahoo_decimals;
    fixes high and low; one bar per start, sorted. meta has regularMarketTime or market_time."""
    meta = meta or {}
    mt = meta.get("regularMarketTime", meta.get("market_time"))
    places = yahoo_decimals(inst)
    by_start = {}
    for i, r in enumerate(rows):
        t = r[0]
        if t % 60 != 0 or (i == len(rows) - 1 and mt is not None and t == mt):
            continue
        if any(x is None for x in r[1:5]):
            continue
        o, h, l, c = [_round_dec(x, places) for x in r[1:5]]
        if inst.get("kind") in ("fx", "metal") and min(o, h, l, c) <= 0:
            continue
        by_start[t] = {"start": ts_to_utc(t), "open": o, "high": max(h, o, c), "low": min(l, o, c),
                       "close": c, "volume": 0 if inst.get("kind") == "fx" else (r[5] or 0)}
    return [by_start[t] for t in sorted(by_start)]


def _span_of(template, utc, cfg, tables=True):
    """The open span (open_utc, close_utc) of a template that holds utc, or None."""
    d = fx_day(utc, "17:00 New York")
    if d is None:
        return None
    for a, b in open_spans(template, d, cfg, tables=tables):
        if a <= utc < b:
            return (a, b)
    return None


def bar_done(start_utc, interval, market_time_utc, now, inst, cfg):
    """True when the bar is over: the data clock passed its end, or it is the last bar
    of its open span (break, weekend, holiday, early close) and now is at least
    DONE_MARGIN_MIN minutes after that close."""
    step = datetime.timedelta(seconds=STEP_S[interval])
    end = start_utc + step
    if market_time_utc is not None and end <= market_time_utc:
        return True
    if now is None or end > now:
        return False
    span = _span_of(inst["data_hours"], start_utc, cfg)
    if span is None:
        return False
    close = span[1]
    return end > close - step and now >= close + datetime.timedelta(minutes=DONE_MARGIN_MIN)


def load_bars(data_dir, symbol, interval, now, rolls=None, include_forming=False, inst=None, cfg=None):
    """Cleaned bars of one cache file: stray bars outside the data hours dropped,
    back-adjusted for rolls (futures), on the plan scale (x yahoo_to_broker), and only
    bars done by now (include_forming adds the forming one). inst (the config row, for
    yahoo_to_broker) and cfg (holidays, early closes) are required."""
    if inst is None or cfg is None:
        raise ValueError("load_bars needs inst= and cfg=")
    doc = load_cache(data_dir, symbol, interval)
    if not doc["bars"]:
        return []
    fetch = last_fetch(doc) or {}
    mt = fetch.get("market_time")
    mt = ts_to_utc(mt) if mt is not None else None
    bars = clean_rows(doc["bars"], {"market_time": fetch.get("market_time")}, interval, inst)
    bars = [b for b in bars if _span_of(inst["data_hours"], b["start"], cfg, tables=False)]
    root = inst_root(inst)
    if root and rolls and str(symbol).endswith("=F"):
        mine = [r for r in rolls if parse_utc(r.get("found_at")) is None or parse_utc(r["found_at"]) <= now]
        bars = adjust_rows(bars, mine, root, interval)
    u = _dec_clean(inst.get("yahoo_to_broker") or 1.0)
    if u != 1:
        q = Decimal(1).scaleb(-int(inst["digits"]))
        for b in bars:
            for k in ("open", "high", "low", "close"):
                b[k] = float((_dec_clean(b[k]) * u).quantize(q, rounding=ROUND_HALF_UP))
    at = parse_utc(fetch.get("at"))
    wall = now if at is None else min(at, now)
    step = datetime.timedelta(seconds=STEP_S[interval])
    out = []
    for b in bars:
        if b["start"] + step <= now and bar_done(b["start"], interval, mt, wall, inst, cfg):
            out.append(b)
        elif include_forming and b["start"] <= now:
            b["forming"] = True
            out.append(b)
    return out


def _overlap(a0, a1, b0, b1):
    """The length of the overlap of [a0, a1) and [b0, b1) as a timedelta (0 or more)."""
    return max(datetime.timedelta(0), min(a1, b1) - max(a0, b0))


def _touched_fx_days(start, end):
    """The 17:00-New-York FX days (date strings) whose bounds overlap [start, end)."""
    out = []
    d = to_ny(start).date() - datetime.timedelta(days=1)
    last = to_ny(end).date() + datetime.timedelta(days=1)
    while d <= last:
        if d.weekday() < 5:
            a, b = fx_day_bounds(d, "17:00 New York")
            if a < end and b > start:
                out.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return out


def open_time_in(template, start, end, cfg):
    """How much open time a template has inside [start, end), as a timedelta: the overlap
    with open_spans of each FX day it touches (tables used)."""
    total = datetime.timedelta(0)
    for e in _touched_fx_days(start, end):
        for a, b in open_spans(template, e, cfg):
            total += _overlap(start, end, a, b)
    return total


def find_outages(bars, inst, interval, cfg):
    """The holes between done bars that miss at least OUTAGE_MIN_SLOTS steps of open data
    time, as (from_utc, to_utc); a hole touching a Yahoo no-data day is never one."""
    step = datetime.timedelta(seconds=STEP_S[interval])
    need = step * OUTAGE_MIN_SLOTS[interval]
    template = inst["data_hours"]
    out = []
    for prev, nxt in zip(bars, bars[1:]):
        h0, h1 = prev["start"] + step, nxt["start"]
        if h1 - h0 < need:
            continue
        if hours_template(template)["group"] == "cme":
            cuts = [c for e in _touched_fx_days(h0, h1) for c in yahoo_no_data_cuts(e)]
            if any(a < h1 and b > h0 for a, b in cuts):
                continue
        if open_time_in(template, h0, h1, cfg) >= need:
            out.append((h0, h1))
    return out


def _daily_flags(day, bars, inst, cfg, outages):
    """The flags of one daily bar (part 02 4.2): partial, holiday, early_close, outage,
    roll, late_start."""
    start, end = day["start"], day["end"]
    expected = open_time_in(inst["data_hours"], start, end, cfg).total_seconds() / 3600.0
    flags = []
    if expected == 0:
        flags.append("holiday")
    elif day["hours"] < 0.75 * expected:
        flags.append("partial")
    group = hours_template(inst["data_hours"])["group"]
    touched = _touched_fx_days(start, end)
    if any(early_close_utc(e, group, cfg) is not None for e in touched):
        flags.append("early_close")
    if any(a < end and b > start for a, b in outages):
        flags.append("outage")
    if any("roll" in (b.get("flags") or []) for b in bars):
        flags.append("roll")
    opens = [max(a, start) for e in touched for a, b in open_spans(inst["hours"], e, cfg)
             if a < end and b > start]
    if opens and bars[0]["start"] > min(opens) + datetime.timedelta(minutes=60):
        flags.append("late_start")
    return flags


def daily_from_hourly(bars_1h, day_end, inst, cfg, include_forming=False, now=None):
    """Daily bars {fx_day, start, end, open, high, low, close, hours, flags} from done 1h
    bars, cut at day_end. The newest FX day is left out while it is forming (it holds now,
    or, with no now, its last bar is a forming bar or ends before its last data close)."""
    check_day_end(day_end)
    groups = {}
    for b in bars_1h:
        d = fx_day(b["start"], day_end)
        if d is not None:
            groups.setdefault(d, []).append(b)
    outages = find_outages([b for b in bars_1h if not b.get("forming")], inst, "1h", cfg)
    out = []
    for d in sorted(groups):
        bars = sorted(groups[d], key=lambda b: b["start"])
        start, end = fx_day_bounds(d, day_end)
        day = {"fx_day": d, "start": start, "end": end, "open": bars[0]["open"],
               "high": max(b["high"] for b in bars), "low": min(b["low"] for b in bars),
               "close": bars[-1]["close"], "hours": len(bars), "flags": []}
        day["flags"] = _daily_flags(day, bars, inst, cfg, outages)
        out.append(day)
    if out and _day_forming(out[-1], groups[out[-1]["fx_day"]], inst, cfg, now):
        if include_forming:
            out[-1]["flags"].append("forming")
        else:
            out.pop()
    return out


def _day_forming(day, bars, inst, cfg, now):
    """True when a daily bar is not over yet: now is before its end; with no now, its
    last 1h bar is forming or ends before the last data close inside the day."""
    if now is not None:
        return now < day["end"]
    last = max(bars, key=lambda b: b["start"])
    if last.get("forming"):
        return True
    closes = [min(b, day["end"]) for e in _touched_fx_days(day["start"], day["end"])
              for a, b in open_spans(inst["data_hours"], e, cfg) if a < day["end"] and b > day["start"]]
    return bool(closes) and last["start"] + datetime.timedelta(hours=1) < max(closes)


def data_age_min(at_utc, now):
    """How many minutes old a price is (at least 0)."""
    return max(0.0, (now - at_utc).total_seconds() / 60.0)


def _plan_bid(raw, inst):
    """A raw Yahoo price -> (plan-scale bid, Yahoo price rounded to yahoo_decimals)."""
    y = _round_dec(raw, yahoo_decimals(inst))
    u = _dec_clean(inst.get("yahoo_to_broker") or 1.0)
    if u == 1:
        return y, y
    q = Decimal(1).scaleb(-int(inst["digits"]))
    return float((_dec_clean(y) * u).quantize(q, rounding=ROUND_HALF_UP)), y


def latest_price(data_dir, inst, now, cfg):
    """The newest Yahoo price of the instrument: {bid (plan scale), yahoo, at, age_min,
    source, contract} from the newest 5m or 1h fetch; in a replay (the cache is newer than
    now) the close of the last bar done by now, at its end. None when there is no data."""
    best = None
    for interval in ("5m", "1h"):
        f = last_fetch(load_cache(data_dir, inst["yahoo"], interval))
        if f and f.get("market_time") is not None and f.get("price") is not None:
            if best is None or f["market_time"] > best[1]["market_time"]:
                best = (interval, f)
    futures = inst.get("price_source") == "futures"
    if best is not None:
        at = ts_to_utc(best[1]["market_time"])
        fetched = parse_utc(best[1].get("at"))
        if at <= now and (fetched is None or fetched <= now):
            bid, y = _plan_bid(best[1]["price"], inst)
            return {"bid": bid, "yahoo": y, "at": at, "age_min": data_age_min(at, now),
                    "source": "yahoo " + best[0], "contract": best[1].get("short_name") if futures else None}
    last = None
    for interval in ("5m", "1h"):
        bars = load_bars(data_dir, inst["yahoo"], interval, now, inst=inst, cfg=cfg)
        if bars:
            end = bars[-1]["start"] + datetime.timedelta(seconds=STEP_S[interval])
            if last is None or end > last[1]:
                last = (interval, end, bars[-1])
    if last is None:
        return None
    raw = last[2]["close"]
    u = _dec_clean(inst.get("yahoo_to_broker") or 1.0)
    y = float(_dec_clean(raw) / u) if u != 1 else raw
    contract = None
    if futures:
        newest = None
        for interval in ("5m", "1h"):
            for f in load_cache(data_dir, inst["yahoo"], interval)["fetches"]:
                t = parse_utc(f.get("at"))
                if t is not None and t <= now and (newest is None or t > newest):
                    newest, contract = t, f.get("short_name")
    return {"bid": raw, "yahoo": _round_dec(y, yahoo_decimals(inst)), "at": last[1],
            "age_min": data_age_min(last[1], now), "source": "yahoo " + last[0], "contract": contract}


def coarse_note(inst, stop_points):
    """The warning for a tight stop on a pair with rough Yahoo data (COARSE), else None."""
    if inst.get("key") not in COARSE:
        return None
    pip = _dec_clean(inst.get("pip") or 0.0001)
    point = _dec_clean(inst.get("point") or Decimal(1).scaleb(-int(inst.get("digits", 5))))
    if abs(_dec_clean(stop_points)) * point < COARSE_MIN_STOP_PIPS * pip:
        return ("Yahoo prices for %s are rough (about one price a minute). A stop under %d pips "
                "can be hit without Yahoo showing it." % (inst["key"], COARSE_MIN_STOP_PIPS))
    return None


# ---- rolls (part 02 section 5)

MONTH_CODES = {"Jan": "F", "Feb": "G", "Mar": "H", "Apr": "J", "May": "K", "Jun": "M",
               "Jul": "N", "Aug": "Q", "Sep": "U", "Oct": "V", "Nov": "X", "Dec": "Z"}
ROLL_MATCH_BARS = 3          # BZ: the old contract still matches on the newest 3 shared bars
ROLL_CHECK_HOURS = 6         # BZ: the match test at most once every 6 hours (not every_run)
ROLL_SAME_DAYS = 3           # a roll of the root within 3 days is the same roll


def rolls_path(data_dir):
    """The path of data/rolls.json."""
    return os.path.join(data_dir, "rolls.json")


def load_rolls_doc(data_dir):
    """The whole rolls.json doc {rolls, checked, updated_at}; an empty one when missing."""
    doc = load_json(rolls_path(data_dir), None) or {}
    if not isinstance(doc, dict) or not isinstance(doc.get("rolls", []), list):
        raise ValueError("%s is broken. Delete it: the rolls are found again." % rolls_path(data_dir))
    doc.setdefault("rolls", [])
    doc.setdefault("checked", {})
    return doc


def load_rolls(data_dir):
    """The list of rolls in rolls.json, sorted by first_bar_new ([] when missing)."""
    return sorted(load_rolls_doc(data_dir)["rolls"], key=lambda r: r.get("first_bar_new") or "")


def save_rolls(data_dir, rolls, checked=None, now=None):
    """Write rolls.json: the rolls sorted by first_bar_new; checked kept from the file
    unless given; updated_at = now (the real clock when None)."""
    doc = load_rolls_doc(data_dir)
    if checked is None:
        checked = doc.get("checked") or {}
    out = {"updated_at": utc_iso(now or utc_now()),
           "rolls": sorted(rolls, key=lambda r: r.get("first_bar_new") or ""), "checked": checked}
    save_json_atomic(rolls_path(data_dir), out)


def contract_from_name(short_name, root, suffix):
    """The single contract named by a Yahoo shortName: "Gold Dec 26" -> "GCZ26.CMX";
    None when the name has no month (BZ=F) or is null."""
    if not short_name:
        return None
    m = re.search(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{2})\s*$", str(short_name))
    if not m:
        return None
    return "%s%s%s%s" % (root, MONTH_CODES[m.group(1)], m.group(2), suffix)


def cme_business_day(d):
    """True for Monday to Friday when HOLIDAYS does not shut the CME all day."""
    return d.weekday() < 5 and "cme" not in HOLIDAYS.get(d.isoformat(), {}).get("closed", [])


def add_business_days(d, n):
    """The CME business day n business days after d (n below 0: before it)."""
    step = datetime.timedelta(days=1 if n >= 0 else -1)
    left = abs(n)
    while left:
        d += step
        if cme_business_day(d):
            left -= 1
    return d


def _month_add(year, month, n):
    """(year, month) moved by n months."""
    k = year * 12 + (month - 1) + n
    return k // 12, k % 12 + 1


def _last_business_day(year, month):
    """The last CME business day of a month."""
    y, m = _month_add(year, month, 1)
    d = datetime.date(y, m, 1) - datetime.timedelta(days=1)
    while not cme_business_day(d):
        d -= datetime.timedelta(days=1)
    return d


def roll_dates(rule, year, month):
    """For the contract that delivers in (year, month): (window_start, window_end, Y) as
    dates, by its roll rule (part 02 5.2). Y is the day Yahoo's daily series switches."""
    py, pm = _month_add(year, month, -1)
    if rule in ("gc_fnd", "si_hg"):
        y_day = _last_business_day(py, pm)
        return add_business_days(y_day, -10), add_business_days(y_day, 1), y_day
    if rule == "cl_ltd":
        d25 = datetime.date(py, pm, 25)
        last = add_business_days(d25, -3 if cme_business_day(d25) else -4)
        back = 8
    elif rule == "bz_ltd":
        last = _last_business_day(*_month_add(year, month, -2))
        back = 10
    elif rule == "ng_ltd":
        last = add_business_days(datetime.date(year, month, 1), -3)
        back = 8
    else:
        raise ValueError('Unknown roll_rule "%s". Use gc_fnd, si_hg, cl_ltd, bz_ltd or ng_ltd.' % rule)
    return add_business_days(last, -back), add_business_days(last, 1), add_business_days(last, 1)


def _cycle_contracts(inst, first_year, last_year):
    """[(year, month, symbol)] of the cycle contracts in those years, in order."""
    fut = inst["futures"]
    code_month = dict((c, i + 1) for i, c in enumerate("FGHJKMNQUVXZ"))
    months = sorted(code_month[c] for c in fut["cycle"])
    out = []
    for y in range(first_year, last_year + 1):
        for m in months:
            out.append((y, m, "%s%s%02d%s" % (fut["root"], "FGHJKMNQUVXZ"[m - 1], y % 100, fut["suffix"])))
    return out


def _windows(inst, first_year, last_year):
    """[(old, new, window_start_utc, window_end_utc, Y date)] for each cycle contract."""
    cs = _cycle_contracts(inst, first_year, last_year + 1)
    out = []
    for (y, m, old), (_, _, new) in zip(cs, cs[1:]):
        a, b, y_day = roll_dates(inst["futures"]["roll_rule"], y, m)
        out.append((old, new, ny_to_utc(at_time(a - datetime.timedelta(days=1), "17:00")),
                    ny_to_utc(at_time(b, "17:00")), y_day))
    return out


def expected_front(root, utc, inst):
    """The cycle contract the calendar rule expects as Yahoo's front at utc: the one whose
    search window END is the first one after utc ("GCZ26.CMX" on 2026-11-11)."""
    for old, new, a, b, y_day in _windows(inst, utc.year - 1, utc.year + 1):
        if b > utc:
            return old
    raise ValueError("No %s contract found after %s." % (root, utc_iso(utc)))


def expected_rolls(root, inst, from_utc, to_utc):
    """Each expected roll (old, new, window_start, window_end, Y) whose search window
    overlaps [from_utc, to_utc]. Y is the date the daily series switches."""
    return [w for w in _windows(inst, from_utc.year - 1, to_utc.year + 1)
            if w[2] <= to_utc and w[3] >= from_utc]


def _roll_bar(row, places):
    """A raw [t, o, h, l, c, v] row -> a bar dict rounded to places decimals."""
    o, h, l, c = [_round_dec(x, places) for x in row[1:5]]
    return {"start": ts_to_utc(row[0]), "open": o, "high": max(h, o, c), "low": min(l, o, c),
            "close": c, "volume": row[5] or 0}


def adjust_rows(rows, rolls, root, interval="1h"):
    """Back-adjusted copies of cleaned bars (dicts): each bar times the ratios of the rolls
    of root that start later. The 1h bar at a roll is its replacement, or keeps the flag
    "roll"; 5m bars in that hour get the flag. The cache is never edited."""
    places = YAHOO_DECIMALS.get(root, 2)
    mine = []
    for r in rolls or []:
        if r.get("root") == root and parse_utc(r.get("first_bar_new")) is not None:
            mine.append((parse_utc(r["first_bar_new"]), r))
    out = []
    for bar in rows:
        b = dict(bar)
        start = b["start"]
        hit = None
        for at, r in mine:
            if (interval == "1h" and at == start) or (interval == "5m" and at <= start < at + datetime.timedelta(hours=1)):
                hit = r
        if hit is not None and interval == "1h" and (hit.get("replacement") or {}).get("1h"):
            b = _roll_bar(hit["replacement"]["1h"], places)
        elif hit is not None and interval in ("1h", "5m"):
            b["flags"] = sorted(set((b.get("flags") or []) + ["roll"]))
        factor = Decimal(1)
        for at, r in mine:
            if at > start:
                factor *= Decimal(repr(float(r["ratio"])))
        if factor != 1:
            q = Decimal(1).scaleb(-places)
            for k in ("open", "high", "low", "close"):
                b[k] = float((Decimal(repr(float(b[k]))) * factor).quantize(q, rounding=ROUND_HALF_UP))
        out.append(b)
    return out


ROLL_AWAY = {"stop_loss": "entry", "take_profit": "entry", "stop_at_open": "entry", "chart_stop": "chart_entry"}


def _get_path(item, path):
    """item["a"]["b"] for the path "a.b", or None."""
    cur = item
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _set_path(item, path, value):
    """Set item["a"]["b"] = value for the path "a.b" (the parents must exist)."""
    parts = path.split(".")
    cur = item
    for part in parts[:-1]:
        cur = cur[part]
    cur[parts[-1]] = value


def rebase(item, rolls, fields, inst=None):
    """Move an open item's plan-scale fields (dotted paths) by each roll it has not had:
    by ratio for a new roll id, by ratio / ratio_applied for a changed one. inst is the
    item's instrument row: only the rolls of its root count (all of load_rolls may be
    given), and the moved levels are rounded to its tick. Returns (new item, ids that
    changed something)."""
    if inst is None:
        raise ValueError("rebase needs the instrument row")
    new = copy.deepcopy(item)
    applied = dict(new.get("rolls_applied") or {})
    root = inst_root(inst)
    if not root:
        new["rolls_applied"] = applied
        return new, []
    mine = [r for r in (rolls or []) if r.get("root") == root]
    mult = Decimal(1)
    ids = []
    for r in sorted(mine, key=lambda r: r.get("first_bar_new") or ""):
        ratio = float(r["ratio"])
        done = applied.get(r["id"])
        if done is None:
            mult *= Decimal(repr(ratio))
        elif float(done) != ratio:
            mult *= Decimal(repr(ratio)) / Decimal(repr(float(done)))
        else:
            continue
        applied[r["id"]] = ratio
        ids.append(r["id"])
    new["rolls_applied"] = applied
    if not ids:
        return new, ids
    plain = [f for f in fields if f.split(".")[-1] not in ROLL_AWAY]
    away = [f for f in fields if f.split(".")[-1] in ROLL_AWAY]
    for f in plain + away:
        v = _get_path(new, f)
        if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        x = _dec_clean(v) * mult
        last = f.split(".")[-1]
        entry_path = ".".join(f.split(".")[:-1] + [ROLL_AWAY[last]]) if last in ROLL_AWAY else None
        entry = _get_path(new, entry_path) if entry_path else None
        if entry is not None and float(x) != float(entry):
            _set_path(new, f, round_away(float(x), entry, inst))
        else:
            _set_path(new, f, round_price(float(x), inst))
    return new, ids


ROLL_WORDS = {"GC": "gold", "SI": "silver", "PL": "platinum", "PA": "palladium", "HG": "copper",
              "CL": "oil", "BZ": "Brent", "NG": "gas"}


def roll_tolerance(inst):
    """Two Yahoo closes match when they differ by at most half a Yahoo step (GC: 0.05)."""
    return 0.5 * 10 ** -yahoo_decimals(inst) + 1e-9


def _contract_bars(data_dir, symbol, inst, now, w0, w1):
    """The 1h bars of a single contract in [w0, w1], fetched into its own cache file;
    None when Yahoo has no data for it (a 404, or no bars in the window)."""
    doc = load_cache(data_dir, symbol, "1h")
    try:
        doc = fetch_into(data_dir, doc, symbol, "1h", now, None, utc_to_ts(w0), utc_to_ts(w1) + 60)
    except NoData:
        return None
    bars = [b for b in clean_rows(doc["bars"], {}, "1h", inst) if w0 <= b["start"] <= w1]
    return bars or None


def _switch_bar(cont, old_by, new_by, tol):
    """(index of the switch bar S in cont, mixed) or None: S is the first bar on the new
    contract after the last bar on the old one (old_by None: a bar not on new stands in)."""
    def on(b, by):
        x = by.get(b["start"]) if by else None
        return x is not None and abs(b["close"] - x["close"]) <= tol
    marks = []
    for b in cont:
        is_new = on(b, new_by)
        is_old = (not is_new) if old_by is None else on(b, old_by)
        marks.append("both" if is_new and is_old else ("new" if is_new else ("old" if is_old else "")))
    olds = [i for i, m in enumerate(marks) if m == "old"]
    start = olds[-1] + 1 if olds else 0
    news = [i for i in range(start, len(cont)) if marks[i] == "new"]
    if not news:
        return None
    if olds and start < len(cont):
        b = cont[start]
        o, n = (old_by or {}).get(b["start"]), new_by.get(b["start"])
        if old_by is not None and o is not None:
            # open on old, close off old (on new, or new has no bar there): the switch is inside
            if abs(b["open"] - o["open"]) <= tol and abs(b["close"] - o["close"]) > tol \
                    and (n is None or abs(b["close"] - n["close"]) <= tol):
                return start, True
        elif old_by is None and n is not None and marks[start] == "new":
            return start, abs(b["open"] - n["open"]) > tol
    return news[0], False


def _roll_ratio(cont, s, old_by, new_by):
    """(ratio, old_price, new_price, prices_at, exact) for a switch at cont[s] (part 02
    5.5 step 5); old_by None: the continuous bars before S stand in for old."""
    at = cont[s]["start"]
    def pair(b):
        o = (old_by or {}).get(b["start"]) if old_by is not None else (b if b["start"] < at else None)
        n = new_by.get(b["start"])
        return (o["close"], n["close"]) if o is not None and n is not None else None
    before = [b for b in cont[:s] if b["start"] >= at - datetime.timedelta(hours=12) and pair(b)]
    if before:
        b = before[-1]
    else:
        after = [b for b in cont[s:] if pair(b)]
        b = after[0] if after else None
    if b is not None:
        o, n = pair(b)
        return n / o, o, n, b["start"], old_by is not None
    if s == 0:
        return None
    o, n = cont[s - 1]["close"], cont[s]["close"]
    return n / o, o, n, cont[s]["start"], False


def _newest_fetch(data_dir, symbol):
    """The newest fetch record (by "at") of the 1h and 5m caches of a symbol, or None."""
    best = None
    for interval in ("1h", "5m"):
        f = last_fetch(load_cache(data_dir, symbol, interval))
        if f and (best is None or (f.get("at") or "") > (best.get("at") or "")):
            best = f
    return best


def _name_change(data_dir, symbol):
    """The shortNames of the two newest fetches (1h and 5m caches, by "at") that have one,
    as (older, newer), when they differ. None on a first run (one name only) or no change:
    a forward roll is only a change between two of our own fetches."""
    rows = []
    for interval in ("1h", "5m"):
        for f in load_cache(data_dir, symbol, interval)["fetches"]:
            if f.get("short_name"):
                rows.append((f.get("at") or "", f["short_name"]))
    rows.sort()
    if len(rows) < 2 or rows[-2][1] == rows[-1][1]:
        return None
    return rows[-2][1], rows[-1][1]


def _bz_still_old(data_dir, inst, old, cont, now):
    """BZ: True when the old contract's 1h closes match BZ=F on the newest shared bars
    (no roll yet); False when old is gone or does not match."""
    bars = _contract_bars(data_dir, old, inst, now, now - datetime.timedelta(hours=24), now)
    if not bars:
        return False
    by = dict((b["start"], b) for b in bars)
    shared = [b for b in cont if b["start"] in by][-ROLL_MATCH_BARS:]
    tol = roll_tolerance(inst)
    return bool(shared) and all(abs(b["close"] - by[b["start"]]["close"]) <= tol for b in shared)


def check_roll_forward(data_dir, inst, cfg, now, offline, every_run=False):
    """After a refresh of a futures symbol: find a roll that just happened (a shortName
    change, or for BZ a failed price match inside a roll window), measure it with the old
    and new contracts' own bars, save it in rolls.json and return the record (else None)."""
    if offline:
        return None
    fut = inst["futures"]
    root, yahoo = fut["root"], inst["yahoo"]
    word = ROLL_WORDS.get(root, inst.get("name", root).lower())
    rdoc = load_rolls_doc(data_dir)
    mine = sorted([r for r in rdoc["rolls"] if r.get("root") == root], key=lambda r: r.get("first_bar_new") or "")
    raw = load_cache(data_dir, yahoo, "1h")["bars"]
    cont = clean_rows(raw, {}, "1h", inst)
    first = cont[0]["start"] if cont else now
    old_name = new_name = None
    if fut.get("shortname_month", True):
        pair = _name_change(data_dir, yahoo)
        if pair is None:
            return None
        old_name, new_name = pair
        old = contract_from_name(old_name, root, fut["suffix"])
        cur = contract_from_name(new_name, root, fut["suffix"])
        if old is None or cur is None or old == cur or any(r.get("new") == cur for r in mine):
            return None
        new, found_by = cur, "shortname"
    else:
        wins = [w for w in expected_rolls(root, inst, now, now) if w[2] <= now <= w[3]]
        if not wins:
            return None
        checked = rdoc["checked"].setdefault(root, {"history_from": None, "history_to": None, "last_forward": None})
        last = parse_utc(checked.get("last_forward"))
        if not every_run and last is not None and now - last < datetime.timedelta(hours=ROLL_CHECK_HOURS):
            return None
        checked["last_forward"] = utc_iso(now)
        old, new = wins[0][0], wins[0][1]
        if any(r.get("new") == new for r in mine) or _bz_still_old(data_dir, inst, old, cont, now):
            save_rolls(data_dir, rdoc["rolls"], rdoc["checked"], now)
            return None
        found_by = "match"
    starts = [w[2] for w in expected_rolls(root, inst, now - datetime.timedelta(days=400), now) if w[1] == new]
    w0 = starts[-1] if starts else ny_to_utc(at_time(add_business_days(to_ny(now).date(), -10), "00:00"))
    w0 = max(w0, first)
    old_bars = _contract_bars(data_dir, old, inst, now, w0, now)
    new_bars = _contract_bars(data_dir, new, inst, now, w0, now)
    if new_bars is None:
        if found_by == "match":
            save_rolls(data_dir, rdoc["rolls"], rdoc["checked"], now)
        raise NoData("Yahoo %s moved to a new contract, but Yahoo has no data for it. "
                     "The roll size will come from daily prices." % word)
    old_by = dict((b["start"], b) for b in old_bars) if old_bars else None
    new_by = dict((b["start"], b) for b in new_bars)
    cw = [b for b in cont if w0 <= b["start"] <= now]
    hit = _switch_bar(cw, old_by, new_by, roll_tolerance(inst))
    if hit is None:
        return None
    s, mixed = hit
    measured = _roll_ratio(cw, s, old_by, new_by)
    if measured is None:
        return None
    ratio, op, np_, at, exact = measured
    S = cw[s]
    rep = None
    if mixed:
        rep = [r for r in load_cache(data_dir, new, "1h")["bars"] if r[0] == utc_to_ts(S["start"])]
        rep = rep[0] if rep else None
    note = "" if old_bars else "The old %s contract is gone from Yahoo. The roll size is an estimate." % word
    rec = {"id": "%s-%s" % (root, S["start"].date().isoformat()), "root": root, "yahoo": yahoo,
           "old": old, "new": new, "old_name": old_name, "new_name": new_name,
           "first_bar_new": utc_iso(S["start"]), "ratio": ratio, "old_price": op, "new_price": np_,
           "prices_at": utc_iso(at), "found_by": found_by,
           "confidence": "exact" if exact else "estimated", "replaced_bar": rep is not None,
           "found_at": utc_iso(now), "note": note, "replacement": {"1h": rep}}
    if mixed and rep is None:
        rec["note"] = (rec["note"] + " The switch bar is flagged roll.").strip()
    return _keep_roll(data_dir, rdoc, rec, now)


def _keep_roll(data_dir, rdoc, rec, now):
    """Add a roll record to rolls.json: a roll of the root within 3 days is replaced only
    by an exact record over an estimated one (keeping its id). Returns the saved record or None."""
    at = parse_utc(rec["first_bar_new"])
    rolls = list(rdoc["rolls"])
    for i, r in enumerate(rolls):
        other = parse_utc(r.get("first_bar_new"))
        if r.get("root") == rec["root"] and other is not None and abs(other - at) <= datetime.timedelta(days=ROLL_SAME_DAYS):
            if rec["confidence"] == "exact" and r.get("confidence") == "estimated":
                rec = dict(rec, id=r["id"])
                rolls[i] = rec
                save_rolls(data_dir, rolls, rdoc["checked"], now)
                return rec
            return None
    rolls.append(rec)
    save_rolls(data_dir, rolls, rdoc["checked"], now)
    return rec


HISTORY_PRE_DAYS = 20        # business days before a window that give the noise
HISTORY_MIN_SIGMA = 0.0005
HISTORY_MIN_TAU = 0.0025


def _median(xs):
    """The median of a non-empty list of numbers."""
    s = sorted(xs)
    k = len(s) // 2
    return s[k] if len(s) % 2 else (s[k - 1] + s[k]) / 2.0


def _settle_utc(inst, d):
    """The settle time of the futures row on date d, as naive UTC."""
    city, hhmm = inst["futures"]["settle"]
    return city_to_utc(city, at_time(d, hhmm))


def _settle_value(by_end, inst, d, cfg):
    """s(d): the mean close of the 1h bars that end just before and just after the settle
    time (Friday: the last close before 17:00 New York); None on an early-close day or
    when a bar is missing. by_end maps each bar's end to its close."""
    if early_close_utc(d, "cme", cfg) is not None:
        return None
    hour = datetime.timedelta(hours=1)
    if d.weekday() == 4:
        return by_end.get(ny_to_utc(at_time(d, "17:00")))
    t0 = _settle_utc(inst, d).replace(minute=0, second=0, microsecond=0)
    before, after = by_end.get(t0), by_end.get(t0 + hour)
    if before is None or after is None:
        return None
    return (before + after) / 2.0


def _daily_closes(data_dir, inst, now, cfg, offline):
    """D: {New York date: Yahoo 1d close} from the continuous 1d cache (refreshed when
    online; a failed refresh keeps the cache)."""
    try:
        doc = refresh(data_dir, inst["yahoo"], "1d", now, cfg, offline)
    except ValueError:
        doc = load_cache(data_dir, inst["yahoo"], "1d")
    out = {}
    for b in clean_rows(doc["bars"], {}, "1d", inst):
        out[to_ny(b["start"]).date()] = b["close"]
    return out


def _gap_run(days, g, tau):
    """The longest run of days (in order, data days only) with abs(g) > tau, all of one
    sign: (first index, last index) into days, or None."""
    best, i = None, 0
    while i < len(days):
        if abs(g[days[i]]) > tau:
            j = i
            while j + 1 < len(days) and abs(g[days[j + 1]]) > tau and (g[days[j + 1]] > 0) == (g[days[i]] > 0):
                j += 1
            if best is None or j - i > best[1] - best[0]:
                best = (i, j)
            i = j + 1
        else:
            i += 1
    return best


def _history_switch_bar(cont, inst, first_day, m):
    """The 1h bar where the series jumped by about m, searched from the settle time of
    the business day before first_day to the settle time of first_day: (bar, mixed) or None."""
    a = _settle_utc(inst, add_business_days(first_day, -1))
    b = _settle_utc(inst, first_day)
    moves = []
    for prev, bar in zip(cont, cont[1:]):
        if a <= bar["start"] < b and prev["close"] > 0 and bar["close"] > 0:
            moves.append((math.log(bar["close"] / prev["close"]), bar))
    if not moves:
        return None
    near = min(moves, key=lambda x: abs(x[0] - m))
    if abs(near[0]) < 0.5 * abs(m):
        near = max(moves, key=lambda x: x[0] if m > 0 else -x[0])
    bar = near[1]
    mixed = bar["open"] > 0 and abs(math.log(bar["close"] / bar["open"])) >= 0.5 * abs(m)
    return bar, mixed


def _history_window(cont, by_end, D, inst, cfg, w):
    """Search one expected roll window w for a daily gap run (part 02 5.6): a roll
    record, or None for a miss."""
    a_day = to_ny(w[2]).date() + datetime.timedelta(days=1)
    b_day = to_ny(w[3]).date()
    pre_from = add_business_days(a_day, -HISTORY_PRE_DAYS)
    g = {}
    d = pre_from
    last_d = max(D) if D else b_day
    while d <= last_d:
        if cme_business_day(d) and d in D and D[d] > 0:
            s = _settle_value(by_end, inst, d, cfg)
            if s is not None and s > 0:
                g[d] = math.log(s / D[d])
        d += datetime.timedelta(days=1)
    pre = [abs(g[x]) for x in g if x < a_day]
    sigma = max(_median(pre) if pre else 0.0, HISTORY_MIN_SIGMA)
    tau = max(5 * sigma, HISTORY_MIN_TAU)
    inside = sorted(x for x in g if a_day <= x <= b_day)
    run = _gap_run(inside, g, tau)
    if run is None:
        return None
    days = inside[run[0]:run[1] + 1]
    later = sorted(x for x in g if x > days[-1])
    if not later or abs(g[later[0]]) > tau:
        return None
    y, y_prev = later[0], days[-1]
    m = _median([g[x] for x in days])
    s_y = math.exp(g[y]) * D[y]
    s_prev = math.exp(g[y_prev]) * D[y_prev]
    if abs(math.log(D[y] / D[y_prev]) - math.log(s_y / s_prev) - m) > tau:
        return None
    hit = _history_switch_bar(cont, inst, days[0], m)
    if hit is None:
        return None
    bar, mixed = hit
    root = inst["futures"]["root"]
    s0 = math.exp(g[days[0]]) * D[days[0]]
    return {"id": "%s-%s" % (root, bar["start"].date().isoformat()), "root": root, "yahoo": inst["yahoo"],
            "old": w[0], "new": w[1], "old_name": None, "new_name": None,
            "first_bar_new": utc_iso(bar["start"]), "ratio": math.exp(m), "old_price": D[days[0]],
            "new_price": s0, "prices_at": utc_iso(_settle_utc(inst, days[0])), "found_by": "daily_gap",
            "confidence": "estimated", "replaced_bar": False, "found_at": None,
            "note": "The switch bar is flagged roll." if mixed else "", "replacement": {"1h": None}}


def find_rolls_history(data_dir, inst, cfg, now, offline):
    """Search the expected roll windows of the 1h cache that rolls.json has not checked
    yet for a gap between 1h and Yahoo 1d closes (part 02 5.6). Saves and returns the
    new (estimated) roll records; a window whose Y is not in the 1d cache waits."""
    fut = inst.get("futures") or {}
    if not fut.get("history_check"):
        return []
    root = fut["root"]
    cont = clean_rows(load_cache(data_dir, inst["yahoo"], "1h")["bars"], {}, "1h", inst)
    if not cont:
        return []
    D = _daily_closes(data_dir, inst, now, cfg, offline)
    by_end = dict((b["start"] + datetime.timedelta(hours=1), b["close"]) for b in cont)
    rdoc = load_rolls_doc(data_dir)
    checked = rdoc["checked"].setdefault(root, {"history_from": None, "history_to": None, "last_forward": None})
    done_to = checked.get("history_to")
    found = []
    for w in expected_rolls(root, inst, cont[0]["start"], min(now, cont[-1]["start"])):
        if done_to and w[4].isoformat() <= done_to:
            continue
        if not D or max(D) < w[4]:
            break
        rec = _history_window(cont, by_end, D, inst, cfg, w)
        if rec is not None:
            rec["found_at"] = utc_iso(now)
            kept = _keep_roll(data_dir, rdoc, rec, now)
            rdoc = load_rolls_doc(data_dir)
            checked = rdoc["checked"].setdefault(root, checked)
            if kept is not None:
                found.append(kept)
        else:
            checked.setdefault("missed", []).append(w[4].isoformat())
        checked["history_to"] = w[4].isoformat()
        checked["history_from"] = checked.get("history_from") or cont[0]["start"].date().isoformat()
    rdoc["checked"][root] = checked
    save_rolls(data_dir, rdoc["rolls"], rdoc["checked"], now)
    return found


ROLL_GUARD_ATR = 3           # a 1h move above 3 x the 1h ATR inside a roll window may be a roll


def roll_guard(inst, bars_1h, rolls, now, cfg):
    """fx-alerts check: inside an expected roll window, a last 1h bar that moved more
    than 3 x the 1h ATR with no roll found in the last 24 hours may be a roll. Returns
    the line that holds a stop or target ring, else None."""
    root = inst_root(inst)
    if not root or not inst.get("futures") or len(bars_1h) < 2:
        return None
    if not [w for w in expected_rolls(root, inst, now, now) if w[2] <= now <= w[3]]:
        return None
    done = [b for b in bars_1h if not b.get("forming")]
    if len(done) < 2:
        return None
    a = atr_wilder(usable_bars(done)[-ATR_WINDOW["1h"]:], 14)
    if not a:
        return None
    last, prev = done[-1], done[-2]
    if max(abs(last["close"] - last["open"]), abs(last["open"] - prev["close"])) <= ROLL_GUARD_ATR * a:
        return None
    for r in rolls or []:
        at = parse_utc(r.get("first_bar_new"))
        if r.get("root") == root and at is not None and now - datetime.timedelta(hours=24) <= at <= now:
            return None
    return "Yahoo %s may have rolled. Check your MetaTrader price." % ROLL_WORDS.get(root, root)


# ---- broker prices (part 02 section 8)

K_LIMITS = {"fx": 0.01, "metal": 0.03, "energy": 0.20}   # how far k may be from 1, by kind
BROKER_PRICE_MINUTES = 60    # a typed broker price counts as today's this long (D23)


def broker_k(inst, broker_bid, yahoo_bid):
    """k = broker_bid / (yahoo_bid x yahoo_to_broker), not rounded; ValueError when the
    broker price is too far from Yahoo's (K_LIMITS by kind)."""
    b, y = _price_dec(broker_bid), _price_dec(yahoo_bid)
    plan = y * _dec_clean(inst.get("yahoo_to_broker") or 1.0)
    k = float(b) / float(plan)
    limit = K_LIMITS[inst["kind"]]
    if abs(k - 1) > limit:
        q = Decimal(1).scaleb(-int(inst["digits"]))
        raise ValueError("Your broker price %s for %s is far from Yahoo's %s (more than %s%%). "
                         "Check the symbol and the price you typed."
                         % ("{:f}".format(b.normalize()), inst["key"],
                            plan.quantize(q, rounding=ROUND_HALF_UP), format(limit * 100, "g")))
    return k


def to_broker(level, k, inst):
    """A plan-scale entry or plain level on the broker scale: level x k, to the nearest tick."""
    return round_price(float(_dec_clean(level) * Decimal(repr(float(k)))), inst)


def to_broker_away(level, entry, k, inst, entry_broker=None):
    """A stop or target on the broker scale: the broker entry plus or minus the plan
    distance (entry - level), rounded away from the broker entry (D3)."""
    if entry_broker is None:
        entry_broker = to_broker(entry, k, inst)
    x = _dec_clean(entry_broker) - (_dec_clean(entry) - _dec_clean(level))
    return round_away(float(x), entry_broker, inst)


def saved_broker_price(prices, symbol, now, cfg, rolls):
    """The prices.json broker record of symbol when it is at most 60 minutes old, on the
    FX day of now, and no roll of its root came after its yahoo_at (D23); else None."""
    rec = ((prices or {}).get("broker") or {}).get(symbol)
    if not rec:
        return None
    at = parse_utc(rec.get("at"))
    if at is None or at > now or now - at > datetime.timedelta(minutes=BROKER_PRICE_MINUTES):
        return None
    day_end = (cfg or {}).get("day_end", "17:00 New York")
    d = fx_day(now, day_end)
    if d is None or fx_day(at, day_end) != d:
        return None
    row = ((cfg or {}).get("instruments") or {}).get(symbol) or {}
    root = (row.get("futures") or {}).get("root")
    y_at = parse_utc(rec.get("yahoo_at")) or at
    for r in rolls or []:
        fbn = parse_utc(r.get("first_bar_new"))
        if root and r.get("root") == root and fbn is not None and fbn > y_at:
            return None
    return rec


def delay_error(inst, age_min, bars_5m, daily_atr=None):
    """About how far the price may have moved since Yahoo's price: the 5m ATR(14) x
    sqrt(age_min / 5), or with no 5m bars daily_atr x sqrt(age_min / 1440); 0 under a
    minute (or with no ATR). Rounded to the instrument's digits."""
    if age_min is None or age_min < 1:
        return 0.0
    a = atr_wilder(usable_bars(bars_5m)[-ATR_WINDOW["5m"]:], 14) if bars_5m else None
    if a:
        x = a * math.sqrt(age_min / 5.0)
    elif daily_atr:
        x = daily_atr * math.sqrt(age_min / 1440.0)
    else:
        return 0.0
    return _round_dec(x, int(inst.get("digits", 2)))


# ---------------------------------------------------------------- money
# group: money

RATE_PAIRS = {"EUR": ("EURUSD", "mul"), "GBP": ("GBPUSD", "mul"), "AUD": ("AUDUSD", "mul"),
              "NZD": ("NZDUSD", "mul"), "JPY": ("USDJPY", "div"), "CHF": ("USDCHF", "div"),
              "CAD": ("USDCAD", "div")}
MIN_STOP_SPREADS = 3
GAP_ATR = {"fx": 1.5, "metal": 2.0, "energy": 3.0}
GAP_ATR_DAY = 3.0


class TooSmall(ValueError):
    """A refused size: the order would be below min_lot (tools exit 1, not 2). It keeps
    min_lot, min_lot_loss_usd, risk_limit_usd and decided_by for the words."""

    def __init__(self, message, min_lot=None, min_lot_loss_usd=None, risk_limit_usd=None,
                 decided_by=None):
        ValueError.__init__(self, message)
        self.min_lot = min_lot
        self.min_lot_loss_usd = min_lot_loss_usd
        self.risk_limit_usd = risk_limit_usd
        self.decided_by = decided_by


def _cents(d):
    """A Decimal amount of money, half up to cents, as a float (never -0.0)."""
    return float(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) + 0.0


def _num_word(x):
    """A plain number for messages: 24 -> "24", 30.5 -> "30.5"."""
    return format(_dec_clean(x).normalize(), "f")


def _pct_word(x):
    """A percent for messages, at least 1 decimal: 1 -> "1.0", 0.25 -> "0.25"."""
    s = _num_word(x)
    return s if "." in s else s + ".0"


def _pccy(inst):
    """The instrument's profit currency (the quote when the row has none)."""
    return inst.get("profit_currency") or inst["quote"]


def _is_currency(code):
    """True for USD and the currencies of RATE_PAIRS (the ones the pack can turn into USD)."""
    return code == "USD" or code in RATE_PAIRS


def _rate_from_price(currency, price):
    """USD per 1 unit of currency from the price of its RATE_PAIRS pair (exact Decimal)."""
    p = _price_dec(price)
    return p if RATE_PAIRS[currency][1] == "mul" else Decimal(1) / p


def rate_entry(currency, price, at=None, age_h=0.0):
    """One row of the rates dict: {usd, pair, price, at, age_h} plus the exact Decimal
    copy "_usd" the money helpers use. usd is a float with 9 significant digits."""
    d = _rate_from_price(currency, price)
    return {"usd": float(format(d, ".9g")), "pair": RATE_PAIRS[currency][0],
            "price": float(_price_dec(price)), "at": at, "age_h": age_h, "_usd": d}


def rate_age_h(at_utc, now):
    """Market hours from at_utc to now, 1 decimal: the hours between minus each FX
    weekend (Friday 17:00 to Sunday 17:00 New York). Never below 0."""
    if now <= at_utc:
        return 0.0
    gone = datetime.timedelta(0)
    d = to_ny(at_utc).date() - datetime.timedelta(days=7)
    last = to_ny(now).date()
    while d <= last:
        if d.weekday() == 4:
            w0 = ny_to_utc(datetime.datetime.combine(d, datetime.time(17)))
            w1 = ny_to_utc(datetime.datetime.combine(d + datetime.timedelta(days=2), datetime.time(17)))
            lo, hi = max(at_utc, w0), min(now, w1)
            if hi > lo:
                gone += hi - lo
        d += datetime.timedelta(days=1)
    hours = Decimal(str(((now - at_utc) - gone).total_seconds())) / Decimal(3600)
    return float(max(hours, Decimal(0)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def fx_rates(data_dir, now, cfg):
    """USD value of one unit of each currency, from the cache only (latest_price, no
    network): {"USD": {...}, "JPY": {"usd", "pair", "price", "at", "age_h", "_usd"}}.
    A pair that is not in the config or has no cached price is left out."""
    out = {"USD": {"usd": 1.0, "pair": None, "price": None, "at": None, "age_h": 0.0,
                   "_usd": Decimal(1)}}
    rows = cfg.get("instruments") or {}
    for ccy, (key, how) in RATE_PAIRS.items():
        if key not in rows:
            continue
        lp = latest_price(data_dir, instrument(cfg, key), now, cfg)
        if not lp or lp.get("bid") is None:
            continue
        at = lp.get("at")
        at_dt = parse_utc(at) if isinstance(at, str) else at   # (check) str or datetime
        age = rate_age_h(at_dt, now) if at_dt is not None else 0.0
        out[ccy] = rate_entry(ccy, lp["bid"], utc_iso(at_dt), age)
    return out


def usd_per_unit(currency, rates, cfg=None):
    """USD for 1 unit of currency, a Decimal. With cfg, a rate older than
    fx_rates_max_age_hours (market hours) is refused."""
    if currency == "USD":
        return Decimal(1)
    if not _is_currency(currency):
        raise ValueError("%s is not a currency. Set profit_currency and margin_currency to USD "
                         "or a currency code." % (currency,))
    row = (rates or {}).get(currency)
    pair = RATE_PAIRS[currency][0]
    if not row:
        raise ValueError("No USD rate for %s. It comes from %s. Keep %s in your config and run "
                         "once with Yahoo on (not --offline)." % (currency, pair, pair))
    if cfg is not None:
        limit = cfg.get("fx_rates_max_age_hours", 24)
        age = row.get("age_h") or 0.0
        if _dec_clean(age) > _dec_clean(limit):
            raise ValueError("The USD rate for %s is %.1f hours old (your limit is %s). Run once "
                             "with Yahoo on to refresh it." % (currency, age, _num_word(limit)))
    if row.get("_usd") is not None:
        return row["_usd"]
    if row.get("price") is not None:
        return _rate_from_price(currency, row["price"])
    return _dec_clean(row["usd"])


def _base_has_rate(inst):
    """True for an FX pair whose profit currency is the quote and whose base has a USD
    rate (USD itself or a RATE_PAIRS currency): its loss rate moves with the stop."""
    return (inst.get("kind") == "fx" and _pccy(inst) == inst.get("quote")
            and _is_currency(inst.get("base")))


def loss_usd_per_unit(inst, stop_loss, rates, cfg=None):
    """USD for 1 unit of the profit currency for the LOSS at the stop, the safer rate:
    an FX pair takes max(today, base rate / stop) (USD-first pairs: 1 / stop)."""
    ccy = _pccy(inst)
    if ccy == "USD":
        return Decimal(1)
    today = usd_per_unit(ccy, rates, cfg)
    if not _base_has_rate(inst):
        return today
    return max(today, usd_per_unit(inst["base"], rates, cfg) / _price_dec(stop_loss))


def reward_usd_per_unit(inst, take_profit, rates, cfg=None):
    """The mirror of loss_usd_per_unit for the reward: the smaller of today's rate and
    base rate / take_profit on an FX pair (the safe side)."""
    ccy = _pccy(inst)
    if ccy == "USD":
        return Decimal(1)
    today = usd_per_unit(ccy, rates, cfg)
    if not _base_has_rate(inst):
        return today
    return min(today, usd_per_unit(inst["base"], rates, cfg) / _price_dec(take_profit))


def rate_symbols(inst, cfg):
    """The instrument keys whose prices this trade needs for USD rates (profit currency,
    margin currency, the base of an FX pair): GBPJPY -> ["USDJPY", "GBPUSD"]."""
    out = []
    codes = [_pccy(inst), inst.get("margin_currency") or inst.get("base")]
    if inst.get("kind") == "fx":
        codes.append(inst.get("base"))
    for code in codes:
        if code in RATE_PAIRS and RATE_PAIRS[code][0] not in out:
            out.append(RATE_PAIRS[code][0])
    return out


def usd_per_point_per_lot(inst, rates, cfg=None):
    """USD a 1-point move makes on 1 lot (MetaTrader's tick value when tick = point):
    point x contract_size x USD per profit unit, half up to 4 decimals."""
    d = (_dec_clean(inst["point"]) * _dec_clean(inst["contract_size"])
         * usd_per_unit(_pccy(inst), rates, cfg))
    return float(d.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _swap_free(cfg):
    """True when the account is swap-free (D20)."""
    return bool(((cfg or {}).get("account") or {}).get("swap_free"))


def _margin_ccy_usd(inst, price, rates, cfg):
    """USD for 1 unit of the margin currency (part 04 5.1 step 2): the order's own price
    when the rate pair is this instrument; price x the profit rate for a commodity base."""
    mc = inst.get("margin_currency") or inst.get("base")
    if mc == "USD":
        return Decimal(1)
    if mc in RATE_PAIRS and RATE_PAIRS[mc][0] == inst.get("key") and price is not None:
        return _rate_from_price(mc, price)
    if _is_currency(mc) or mc != inst.get("base"):
        return usd_per_unit(mc, rates, cfg)
    if price is None:
        raise ValueError("%s: the margin is in %s. The tool needs a price to count it."
                         % (inst.get("key"), mc))   # (check) words
    return _price_dec(price) * usd_per_unit(_pccy(inst), rates, cfg)


def _commission(inst, lots):
    """Commission for both sides, unrounded Decimal: commission_per_lot_side x lots x 2."""
    return _dec_clean(inst.get("commission_per_lot_side") or 0) * _dec_clean(lots) * 2


def _spread(inst, lots, rates, cfg):
    """The spread's money, unrounded Decimal: spread_points x point x contract_size x
    lots x USD per profit unit (words only: it is already inside the stop distance)."""
    return (_dec_clean(inst["spread_points"]) * _dec_clean(inst["point"])
            * _dec_clean(inst["contract_size"]) * _dec_clean(lots)
            * usd_per_unit(_pccy(inst), rates, cfg))


def _swap(inst, side, lots, nights, rates, cfg, price):
    """The swap for lots over nights (weighted, 6.3), unrounded Decimal, signed as in
    MetaTrader (plus = paid to you). 0 on a swap-free account (D20)."""
    side_word(side)
    if _swap_free(cfg):
        return Decimal(0)
    key = inst.get("key")
    v = _dec_clean(inst.get("swap_long" if side == "buy" else "swap_short") or 0)
    mode = inst.get("swap_mode") or "points"
    if mode == "disabled":
        return Decimal(0)
    if mode == "points":
        per = v * _dec_clean(inst["point"]) * _dec_clean(inst["contract_size"])
        per *= usd_per_unit(_pccy(inst), rates, cfg)
    elif mode == "deposit":
        per = v
    elif mode == "profit":
        per = v * usd_per_unit(_pccy(inst), rates, cfg)
    elif mode == "base":
        base = inst.get("base")
        if _is_currency(base):
            per = v * usd_per_unit(base, rates, cfg)
        elif price is None:
            raise ValueError("%s swaps are in %s. The tool needs a price to count them."
                             % (key, base))   # (check) words
        else:
            per = v * _price_dec(price) * usd_per_unit(_pccy(inst), rates, cfg)
    elif mode == "margin":
        per = v * _margin_ccy_usd(inst, price, rates, cfg)
    elif mode == "interest":
        if price is None:
            raise ValueError("%s swaps are a yearly percent. The tool needs a price to count them."
                             % key)
        per = _price_dec(price) * _dec_clean(inst["contract_size"]) * v / 100 / 360
        per *= usd_per_unit(_pccy(inst), rates, cfg)
    else:
        raise ValueError('%s: swap_mode "%s" is not known. Use points, deposit, profit, base, '
                         'margin, interest or disabled.' % (key, mode))   # (check) words
    return per * _dec_clean(lots) * _dec_clean(nights)


def _fee(inst, lots, nights, cfg):
    """The swap-free fee (D20), unrounded Decimal: fee_per_lot x lots x the nights after
    swap_free_grace_nights. 0 when the account is not swap-free or has no fee."""
    fee = inst.get("swap_free_fee_per_lot")
    if not _swap_free(cfg) or fee is None:
        return Decimal(0)
    paid = max(Decimal(0), _dec_clean(nights) - _dec_clean(inst.get("swap_free_grace_nights") or 0))
    return _dec_clean(fee) * _dec_clean(lots) * paid


def commission_usd(inst, lots):
    """Commission for both sides in USD, half up to cents (E1: 3.00 x 0.68 x 2 = 4.08)."""
    return _cents(_commission(inst, lots))


def spread_usd(inst, lots, rates, cfg=None):
    """The spread's money in USD, half up to cents (words and trade_costs only)."""
    return _cents(_spread(inst, lots, rates, cfg))


def swap_usd(inst, side, lots, nights, rates, cfg, price=None):
    """The swap in USD, signed (plus = paid to you), half up to cents; 0.0 swap-free.
    price (the entry) is needed for the "interest" mode."""
    return _cents(_swap(inst, side, lots, nights, rates, cfg, price))


def swap_free_fee_usd(inst, lots, nights, cfg):
    """The swap-free fee in USD after the grace nights, half up to cents (always a cost)."""
    return _cents(_fee(inst, lots, nights, cfg))



def swap_nights(open_utc, close_utc, inst, cfg):
    """Rollovers held (each Monday to Friday 17:00 New York with open < t <= close),
    the swap_triple_day one counted 3 ("none": every night 1). Naive UTC times."""
    if getattr(open_utc, "tzinfo", None) is not None or getattr(close_utc, "tzinfo", None) is not None:
        raise ValueError("Times must be naive UTC datetimes.")   # (check) words
    if close_utc <= open_utc:
        return 0
    triple = inst.get("swap_triple_day") or "wednesday"   # (check) default
    n = 0
    d = to_ny(open_utc).date()
    last = to_ny(close_utc).date() + datetime.timedelta(days=1)
    while d <= last:
        if d.weekday() < 5:
            t = ny_to_utc(datetime.datetime.combine(d, datetime.time(17)))
            if open_utc < t <= close_utc:
                n += 3 if triple != "none" and FULL_DAY_NAMES[d.weekday()].lower() == triple else 1
        d += datetime.timedelta(days=1)
    return n


def _costs(inst, side, lots, nights, rates, cfg, price):
    """The costs inside a max loss, unrounded Decimal: commission + swap-free fee + the
    swap you pay (a swap credit counts 0). The spread is not here (it is in the price)."""
    return (_commission(inst, lots) + _fee(inst, lots, nights, cfg)
            + max(Decimal(0), -_swap(inst, side, lots, nights, rates, cfg, price)))


def costs_usd(inst, side, lots, nights, rates, cfg, price):
    """orders.json plan.costs_usd: _costs for the order's lots, half up to cents."""
    return _cents(_costs(inst, side, lots, nights, rates, cfg, price))


def trade_costs(inst, side, lots, nights, rates, cfg, price=None):
    """{spread, commission, swap, fee, total} in USD, each half up to cents; total =
    spread + commission + fee - swap on the unrounded cores, rounded once."""
    sp = _spread(inst, lots, rates, cfg)
    co = _commission(inst, lots)
    sw = _swap(inst, side, lots, nights, rates, cfg, price)
    fe = _fee(inst, lots, nights, cfg)
    return {"spread": _cents(sp), "commission": _cents(co), "swap": _cents(sw),
            "fee": _cents(fe), "total": _cents(sp + co + fe - sw)}


def _lots_dec(lots):
    """Lots as a cleaned Decimal; ValueError when not above 0."""
    d = _dec_clean(lots)
    if d <= 0:
        raise ValueError("Lots must be above 0.")
    return d


def _check_stop_side(side, entry, stop_loss):
    """entry and stop_loss as Decimals; ValueError when the stop is on the wrong side."""
    side_word(side)
    e, s = _price_dec(entry), _price_dec(stop_loss)
    if side == "buy" and not s < e:
        raise ValueError("A buy's stop must be below the entry.")
    if side == "sell" and not s > e:
        raise ValueError("A sell's stop must be above the entry.")
    return e, s


def max_loss_core(inst, side, entry, stop_loss, nights, rates, cfg):
    """The max loss of 1 lot at the stop, all costs in, unrounded Decimal (6.7 on the
    cores; the swap with price=entry). max_loss_usd is this x lots, rounded once."""
    e, s = _check_stop_side(side, entry, stop_loss)
    price_loss = abs(e - s) * _dec_clean(inst["contract_size"]) * loss_usd_per_unit(inst, s, rates, cfg)
    return price_loss + _costs(inst, side, 1, nights, rates, cfg, e)


def max_loss_usd(inst, side, lots, entry, stop_loss, nights, rates, cfg):
    """The loss in USD if the stop fills at its price, all costs in (the spread is in
    the price, a swap credit counts 0), half up to cents once."""
    return _cents(_lots_dec(lots) * max_loss_core(inst, side, entry, stop_loss, nights, rates, cfg))


def net_core(inst, side, entry, stop_loss, take_profit, nights, rates, cfg):
    """Reward after costs over the max loss, per 1 lot, unrounded Decimal (6.8)."""
    e, s = _check_stop_side(side, entry, stop_loss)
    t = _price_dec(take_profit)
    if side == "buy" and not t > e:
        raise ValueError("A buy's target must be above the entry.")
    if side == "sell" and not t < e:
        raise ValueError("A sell's target must be below the entry.")
    reward = abs(t - e) * _dec_clean(inst["contract_size"]) * reward_usd_per_unit(inst, t, rates, cfg)
    reward -= _costs(inst, side, 1, nights, rates, cfg, e)
    return reward / max_loss_core(inst, side, e, s, nights, rates, cfg)


def reward_risk_net(inst, side, entry, stop_loss, take_profit, nights, rates, cfg):
    """Reward after costs over the max loss, per 1 lot, half up to 2 decimals."""
    d = net_core(inst, side, entry, stop_loss, take_profit, nights, rates, cfg)
    return float(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def r_multiple(result_usd, max_loss_usd):
    """result / max loss, half up to 2 decimals; None when max_loss_usd <= 0."""
    m = _dec_clean(max_loss_usd)
    if m <= 0:
        return None
    return float((_dec_clean(result_usd) / m).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) + 0.0


def _pl_core(inst, side, entry, exit_price, nights, rates, cfg):
    """1 lot's P/L after the swap and the swap-free fee but before commission,
    unrounded Decimal. A USD-first pair turns it into USD at 1 / exit_price."""
    side_word(side)
    e, x = _price_dec(entry), _price_dec(exit_price)
    move = (x - e) if side == "buy" else (e - x)
    ccy = _pccy(inst)
    if inst.get("kind") == "fx" and inst.get("base") == "USD" and ccy == inst.get("quote"):
        rate = Decimal(1) / x
    else:
        rate = usd_per_unit(ccy, rates, cfg)
    pl = move * _dec_clean(inst["contract_size"]) * rate
    return pl + _swap(inst, side, 1, nights, rates, cfg, e) - _fee(inst, 1, nights, cfg)


def result_core(inst, side, entry, exit_price, nights, rates, cfg):
    """A closed trade of 1 lot after all costs, unrounded Decimal (6.10 on the cores;
    the swap with price=entry). exit_price: the bid for a buy, the ask for a sell."""
    return _pl_core(inst, side, entry, exit_price, nights, rates, cfg) - _commission(inst, 1)


def result_usd(inst, side, lots, entry, exit_price, nights, rates, cfg):
    """A closed trade in USD after all costs, signed (minus = a loss), half up to cents once."""
    return _cents(_lots_dec(lots) * result_core(inst, side, entry, exit_price, nights, rates, cfg))


def position_pl_usd(position, bid, inst, rates, cfg):
    """An open position's P/L in USD at the latest Yahoo bid (plan scale): a sell closes
    at the ask. The swap and fee so far are in; the commission is booked apart."""
    side = position["side"]
    exit_price = bid if side == "buy" else ask_of(bid, inst)
    nights = position.get("swap_nights") or 0
    lots = _lots_dec(position["lots"])
    return _cents(lots * _pl_core(inst, side, position["entry"], exit_price, nights, rates, cfg))


def _margin(inst, lots, price, side, cfg, rates):
    """The margin of lots in USD, unrounded Decimal (5.1, per calc_mode). price is the
    order's entry: a buy's ask, a sell's bid."""
    side_word(side)
    n = _lots_dec(lots)
    p = _price_dec(price)
    key = inst.get("key")
    mode = inst.get("calc_mode")
    lev = _dec_clean(inst.get("leverage") or ((cfg or {}).get("account") or {}).get("leverage") or 100)
    im = inst.get("initial_margin")
    im = None if im is None else _dec_clean(im)
    size = _dec_clean(inst["contract_size"])
    if mode not in ("forex", "forex_no_leverage", "cfd", "cfd_leverage", "futures"):
        raise ValueError('%s: calc_mode "%s" is not known. Use forex, forex_no_leverage, cfd, '
                         'cfd_leverage or futures.' % (key, mode))
    if mode == "futures" and im is None:
        raise ValueError("%s uses calc_mode futures. Copy its Initial margin from MetaTrader "
                         "into initial_margin." % key)
    if mode == "forex":
        m = n * (im if im is not None else size) / lev
    elif mode == "forex_no_leverage":
        m = n * (im if im is not None else size)
    elif mode == "cfd":
        m = n * im if im is not None else n * size * p
    elif mode == "cfd_leverage":
        m = n * im / lev if im is not None else n * size * p / lev
    else:
        m = n * im
    m *= _dec_clean(inst.get("margin_rate") or 1)
    return m * _margin_ccy_usd(inst, p, rates, cfg)


def margin_usd(inst, lots, price, side, cfg, rates):
    """The margin of lots in USD per calc_mode, at the order's entry price, x margin_rate,
    in USD, half up to cents. Each position counts its full margin (no hedge credit)."""
    return _cents(_margin(inst, lots, price, side, cfg, rates))


def margin_level_pct(equity, used_margin):
    """equity / used margin x 100, half up to 2 decimals; None when no margin is in use."""
    u = _dec_clean(used_margin)
    if u <= 0:
        return None
    return float((_dec_clean(equity) / u * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def margin_state(equity, used_margin, cfg):
    """{level_pct, call_equity, stop_out_equity, free_margin, state}: state is "stop_out"
    at or below account.stop_out_pct, "call" at or below margin_call_pct, else "ok"."""
    acc = (cfg or {}).get("account") or {}
    call_pct = _dec_clean(acc.get("margin_call_pct", 100))
    stop_pct = _dec_clean(acc.get("stop_out_pct", 50))
    u, e = _dec_clean(used_margin), _dec_clean(equity)
    level = margin_level_pct(e, u)
    state = "ok"
    if u > 0:
        exact = e / u * 100
        if exact <= stop_pct:
            state = "stop_out"
        elif exact <= call_pct:
            state = "call"
    return {"level_pct": level, "call_equity": _cents(u * call_pct / 100),
            "stop_out_equity": _cents(u * stop_pct / 100), "free_margin": _cents(e - u),
            "state": state}


def _floor_lots(raw, inst):
    """Raw lots DOWN to a whole number of lot_step (cleaned to 9 decimals first, D11).
    0 or less gives 0. No min_lot check here."""
    step = _dec_clean(inst["lot_step"])
    if raw <= 0:
        return Decimal(0)
    steps = (raw / step).quantize(Decimal("0.000000001"), rounding=ROUND_HALF_UP)
    return (steps.to_integral_value(rounding=ROUND_FLOOR) * step).quantize(step.normalize())


def _lot_word(d):
    """Lots as plain text for files and messages: Decimal 0.68 -> "0.68", 100 -> "100"."""
    return format(_dec_clean(d).normalize(), "f")


def _risk_words(inst, min_lot_loss, risk_usd):
    """The 4.4 "by risk" refusal, without the percent line."""
    return ("%s lot is the smallest order for %s. At this stop it loses %s. Your max loss is "
            "%s. So this trade is too big for your account. Do not move the stop closer to "
            "make it fit." % (_lot_word(inst["min_lot"]), inst.get("key"), money(min_lot_loss),
                              money(risk_usd)))


def _loss_per_lot(entry, stop_loss, inst, rates, extra_per_lot_usd, cfg):
    """D13 loss of 1 lot at the stop plus the costs per lot, unrounded Decimal."""
    e, s = _price_dec(entry), _price_dec(stop_loss)
    if e == s:
        raise ValueError("The stop is at the entry price. There is no risk to size.")
    return (abs(e - s) * _dec_clean(inst["contract_size"]) * loss_usd_per_unit(inst, s, rates, cfg)
            + _dec_clean(extra_per_lot_usd))


def lots_for_risk(risk_usd, entry, stop_loss, inst, rates, extra_per_lot_usd=0, cfg=None):
    """The lots that lose at most risk_usd at the stop (D13 loss per unit plus the costs
    per lot), DOWN to lot_step. TooSmall below min_lot; max_lot is not applied here."""
    risk = _dec_clean(risk_usd)
    if risk <= 0:
        raise ValueError("The max loss must be above $0.")
    per_lot = _loss_per_lot(entry, stop_loss, inst, rates, extra_per_lot_usd, cfg)
    lots = _floor_lots(risk / per_lot, inst)
    min_lot = _dec_clean(inst["min_lot"])
    if lots < min_lot:
        loss = min_lot * per_lot
        raise TooSmall(_risk_words(inst, loss, risk), min_lot=float(min_lot),
                       min_lot_loss_usd=_cents(loss), risk_limit_usd=_cents(risk), decided_by=None)
    return lots


SIZE_LIMITS = ("risk_pct", "risk_usd", "max_lot", "margin_cap", "free_margin")


def _risk_money(balance, cfg, risk_pct, risk_usd):
    """(pct, cap) for size_order (D22): the flags may only lower the config limits.
    cap is None when no dollar limit is set."""
    risk = (cfg or {}).get("risk") or {}
    pct = _dec_clean(risk.get("max_risk_per_trade_pct", 1.0))
    if risk_pct is not None:
        if _dec_clean(risk_pct) > pct:
            raise ValueError("Your config allows at most %s%% a trade. To go higher, change "
                             "risk.max_risk_per_trade_pct in fx/config.json." % _pct_word(pct))
        pct = _dec_clean(risk_pct)
    cap = risk.get("max_risk_per_trade_usd")
    cap = None if cap is None else _dec_clean(cap)
    if risk_usd is not None:
        if cap is not None and _dec_clean(risk_usd) > cap:
            raise ValueError("Your config allows at most %s a trade. To go higher, change "
                             "risk.max_risk_per_trade_usd in fx/config.json." % money(cap))
        cap = _dec_clean(risk_usd)
    return pct, cap


def size_order(inst, side, entry, stop_loss, nights, balance, used_margin_usd, rates, cfg,
               risk_pct=None, risk_usd=None, equity=None):
    """The lots for one open order: the smallest of the risk limits (D22), max_lot, the
    margin cap and free margin, with decided_by. TooSmall below min_lot (exit 1)."""
    e, s = _check_stop_side(side, entry, stop_loss)
    bal, used = _dec_clean(balance), _dec_clean(used_margin_usd)
    pct, cap = _risk_money(bal, cfg, risk_pct, risk_usd)
    by_pct = bal * pct / 100
    risk_limit = by_pct if cap is None else min(by_pct, cap)
    if risk_limit <= 0:
        raise ValueError("The max loss must be above $0.")
    extra = _costs(inst, side, 1, nights, rates, cfg, e)
    per_lot = _loss_per_lot(e, s, inst, rates, extra, cfg)
    per_margin = _margin(inst, 1, e, side, cfg, rates)
    cap_pct = _dec_clean(((cfg or {}).get("risk") or {}).get("max_margin_used_pct", 30))
    room = bal * cap_pct / 100 - used
    free = (bal if equity is None else _dec_clean(equity)) - used
    limits = {"risk_pct": _floor_lots(by_pct / per_lot, inst),
              "risk_usd": None if cap is None else _floor_lots(cap / per_lot, inst),
              "max_lot": _dec_clean(inst["max_lot"]),
              "margin_cap": _floor_lots(room / per_margin, inst),
              "free_margin": _floor_lots(free / per_margin, inst)}
    decided = None
    for name in SIZE_LIMITS:
        if limits[name] is not None and (decided is None or limits[name] < limits[decided]):
            decided = name
    lots = limits[decided]
    min_lot = _dec_clean(inst["min_lot"])
    min_loss = min_lot * per_lot
    if lots < min_lot:
        if decided in ("risk_pct", "risk_usd"):
            words = _risk_words(inst, min_loss, risk_limit)
            if decided == "risk_pct":
                words += " At %s%% a trade you need about %s for this trade." % (
                    _pct_word(pct), money(min_loss / pct * 100))
        elif decided == "margin_cap":
            words = ("%s lot of %s needs %s margin. You have room for %s (%s%% of your balance, "
                     "minus margin in use). So this trade does not fit."
                     % (_lot_word(min_lot), inst.get("key"), money(min_lot * per_margin),
                        money(max(room, Decimal(0))), _num_word(cap_pct)))
        else:
            words = ("%s lot of %s needs %s margin. Your free margin is %s. So this trade does "
                     "not fit." % (_lot_word(min_lot), inst.get("key"), money(min_lot * per_margin),
                                   money(max(free, Decimal(0)))))   # (check) words
        raise TooSmall(words, min_lot=float(min_lot), min_lot_loss_usd=_cents(min_loss),
                       risk_limit_usd=_cents(risk_limit), decided_by=decided)
    return {"lots": lots, "decided_by": decided, "risk_limit_usd": _cents(risk_limit),
            "max_loss_usd": _cents(lots * per_lot), "loss_per_lot_usd": _cents(per_lot),
            "min_lot_loss_usd": _cents(min_loss), "margin_usd": _cents(lots * per_margin),
            "margin_per_lot_usd": _cents(per_margin),
            "limits": dict((k, None if v is None else _lot_word(v)) for k, v in limits.items())}


def _points_word(n):
    """Whole points for messages: "1 point", "1,913 points"."""
    return "%s %s" % (format(n, ","), "point" if n == 1 else "points")


def stops_problems(side, entry_type, entry, stop_loss, take_profit, bid_now, inst):
    """The MetaTrader stops-level and side checks (R2 7e) as plain lines; empty when all
    is well. Market orders test against the bid and ask now, pending ones against entry."""
    side_word(side)
    if entry_type not in ("market", "limit", "stop"):
        raise ValueError('The order type must be "market", "limit" or "stop".')   # (check) words
    bid = _price_dec(bid_now)
    ask = _price_dec(ask_of(bid, inst))
    point = _dec_clean(inst["point"])
    lvl_pts = int(_dec_clean(inst.get("stops_level_points") or 0))
    lvl = lvl_pts * point
    out = []

    def check(dist, what):
        if dist <= 0:
            out.append("Your %s is on the wrong side of the price now." % what)
        elif dist < lvl:
            out.append("Your %s is too close. MetaTrader needs it at least %s (%s) from the price. "
                       "It is %s away." % (what, _points_word(lvl_pts), price_text(lvl, inst),
                                           _points_word(abs(to_points(dist, inst)))))

    buy = side == "buy"
    if entry_type == "market":
        ref_sl, ref_tp = (bid, bid) if buy else (ask, ask)
    else:
        p = _price_dec(entry)
        ref_sl = ref_tp = p
        dist = {("buy", "limit"): ask - p, ("sell", "limit"): p - bid,
                ("buy", "stop"): p - ask, ("sell", "stop"): bid - p}[(side, entry_type)]
        above = (side, entry_type) in (("buy", "stop"), ("sell", "limit"))
        if dist <= 0:
            out.append("A %s %s order must be %s the price now (%s %s)." % (
                side, entry_type, "above" if above else "below", "ask" if buy else "bid",
                price_text(ask if buy else bid, inst)))
        elif dist < lvl:   # (check) words
            out.append("Your order price is too close. MetaTrader needs it at least %s (%s) from "
                       "the price. It is %s away." % (_points_word(lvl_pts), price_text(lvl, inst),
                                                      _points_word(abs(to_points(dist, inst)))))
    if stop_loss is not None:
        s = _price_dec(stop_loss)
        check(ref_sl - s if buy else s - ref_sl, "stop")
    if take_profit is not None:
        t = _price_dec(take_profit)
        check(t - ref_tp if buy else ref_tp - t, "target")
    return out


def bar_hits(side, bar, stop_loss, take_profit, inst, margin_points=0):
    """D5 on one Yahoo (bid) bar: "stop", "target", "both" (order unknown: callers count
    it as the stop) or None. A buy's levels fire on the bid, a sell's on the ask. A bar
    that OPENS past a level hit that level first."""
    side_word(side)
    s = _dec_clean(spread_price(inst))
    m = _dec_clean(margin_points) * _dec_clean(inst["point"])
    o, h, lo = _dec_clean(bar["open"]), _dec_clean(bar["high"]), _dec_clean(bar["low"])
    sl = None if stop_loss is None else _dec_clean(stop_loss)
    tp = None if take_profit is None else _dec_clean(take_profit)
    if side == "buy":
        if sl is not None and o <= sl:
            return "stop"
        if tp is not None and o >= tp:
            return "target"
        stop = sl is not None and lo <= sl + m
        target = tp is not None and h >= tp
    else:
        if sl is not None and o + s >= sl:
            return "stop"
        if tp is not None and o + s <= tp:
            return "target"
        stop = sl is not None and h + s >= sl - m
        target = tp is not None and lo + s <= tp
    if stop and target:
        return "both"
    return "stop" if stop else ("target" if target else None)


def pending_fill(side, entry_type, bar, entry, inst):
    """The fill price of an entry order in one bar, or None: a stop or limit fills at
    its price, or at the open when the bar opens past it; market fills at the open
    (a buy at the ask). In the fill bar only a stop hit counts (the safe side)."""
    side_word(side)
    o = _dec_clean(bar["open"])
    if entry_type == "market":
        return ask_of(o, inst) if side == "buy" else round_price(o, inst)
    if entry_type not in ("stop", "limit"):
        raise ValueError('The order type must be "market", "limit" or "stop".')   # (check) words
    s = _dec_clean(spread_price(inst))
    p = _price_dec(entry)
    h, lo = _dec_clean(bar["high"]), _dec_clean(bar["low"])
    if side == "buy" and entry_type == "stop":
        fill = (o + s) if o + s >= p else (p if h + s >= p else None)
    elif side == "buy":
        fill = (o + s) if o + s <= p else (p if lo + s <= p else None)
    elif entry_type == "stop":
        fill = o if o <= p else (p if lo <= p else None)
    else:
        fill = o if o >= p else (p if h >= p else None)
    return None if fill is None else round_price(fill, inst)


def gap_fill(side, bar_open, stop_loss, inst):
    """The fill when a bar opens past the stop (it becomes a market order): a buy at the
    open bid, a sell at the open ask. None when the open is not past the stop."""
    side_word(side)
    o, sl = _dec_clean(bar_open), _dec_clean(stop_loss)
    if side == "buy":
        return round_price(o, inst) if o <= sl else None
    a = o + _dec_clean(spread_price(inst))
    return round_price(a, inst) if a >= sl else None


# ---------------------------------------------------------------- indicators, setups
# group: indicators, setups

# ---- indicators (part 05 section 1)

# A daily bar with one of these flags is used by no indicator and no setup.
SKIP_FLAGS = ("roll", "outage", "partial", "holiday", "forming")
# The ATR window: this many usable bars, ending at the bar the ATR is for.
ATR_WINDOW = {"1d": 250, "1h": 120, "5m": 288}


def usable_bars(bars, outages=None):
    """New copies of the bars without the SKIP_FLAGS ones; each gets "after_gap": True
    on the first bar, after a dropped bar, or at the end of an outage, else False."""
    ends = set(to for _frm, to in (outages or []))
    out = []
    dropped = True
    for bar in bars:
        if any(f in SKIP_FLAGS for f in (bar.get("flags") or ())):
            dropped = True
            continue
        b = dict(bar)
        b["after_gap"] = bool(dropped or b.get("start") in ends)
        dropped = False
        out.append(b)
    return out


def true_range(bar, prev_close):
    """The true range of one bar: high - low, or the widest move from prev_close
    (prev_close None, or the bar after a gap: just high - low). Not rounded."""
    hl = bar["high"] - bar["low"]
    if prev_close is None or bar.get("after_gap"):
        return hl
    return max(hl, abs(bar["high"] - prev_close), abs(bar["low"] - prev_close))


def atr_wilder(bars, n=14):
    """Wilder ATR(n) of usable_bars output (the caller cuts the ATR_WINDOW first): the
    mean of the first n true ranges, then Wilder smoothing. None if under n + 1 bars or 0."""
    if len(bars) < n + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        prev = None if bars[i].get("after_gap") else bars[i - 1]["close"]
        trs.append(true_range(bars[i], prev))
    atr = sum(trs[:n]) / float(n)
    for tr in trs[n:]:
        atr = (atr * (n - 1) + tr) / n
    return atr if atr > 0 else None


def sma(values, n):
    """The mean of the last n values; None when there are fewer than n."""
    if n <= 0 or len(values) < n:
        return None
    return sum(values[-n:]) / float(n)


def ema_series(values, n):
    """The EMA(n) after each value (seed: the SMA of the first n, k = 2 / (n + 1));
    None for the first 2 x n - 1 values, while the EMA still leans on its seed."""
    out = [None] * len(values)
    if n <= 0 or len(values) < n:
        return out
    k = 2.0 / (n + 1)
    e = sum(values[:n]) / float(n)
    for i in range(n - 1, len(values)):
        if i >= n:
            e = (values[i] - e) * k + e
        if i >= 2 * n - 1:
            out[i] = e
    return out


def ema(values, n):
    """The EMA(n) after the last value; None when there are fewer than 2 x n values."""
    if not values:
        return None
    return ema_series(values, n)[-1]


def rsi_series(closes, n=14):
    """Wilder RSI(n) after each close (100 with no losses, 50 when flat); None for the
    first n closes."""
    out = [None] * len(closes)
    if n <= 0 or len(closes) < n + 1:
        return out
    d = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gain = sum(max(x, 0) for x in d[:n]) / float(n)
    loss = sum(max(-x, 0) for x in d[:n]) / float(n)
    for i in range(n, len(closes)):
        if i > n:
            x = d[i - 1]
            gain = (gain * (n - 1) + max(x, 0)) / n
            loss = (loss * (n - 1) + max(-x, 0)) / n
        if loss == 0:
            out[i] = 50.0 if gain == 0 else 100.0
        else:
            out[i] = 100.0 - 100.0 / (1.0 + gain / loss)
    return out


def rsi_wilder(closes, n=14):
    """Wilder RSI(n) after the last close; None when there are fewer than n + 1 closes."""
    if not closes:
        return None
    return rsi_series(closes, n)[-1]


def highest(bars, n, field="high"):
    """The max of field over the last n bars; None when there are fewer than n."""
    if n <= 0 or len(bars) < n:
        return None
    return max(b[field] for b in bars[-n:])


def lowest(bars, n, field="low"):
    """The min of field over the last n bars; None when there are fewer than n."""
    if n <= 0 or len(bars) < n:
        return None
    return min(b[field] for b in bars[-n:])


# ---- setups (part 05 sections 2 and 3)

NEAR_ATR = 0.5            # a breakout level is listed when the close is this near (in ATRs)
CHASE_ATR = 1.0           # more than this past the level: the chase note
MIN_DAILY_BARS = 60       # fewer usable daily bars: no swing setups at all
SWING_BUFFER_ATR = 0.25   # swing-low, swing-high, flag-far and bar-far stops: this far out
SWING_VALID_DAYS = 3      # FX days a swing hit waits for its close
OR_MAX_DAILY_ATR = 0.6    # an opening range wider than this x the daily ATR is skipped
OR_MIN_SPREADS = 8        # an opening range narrower than this x the spread is skipped
NEAR_ATR_5M = 1.0         # a day hit with no trigger: the close this near (in 5m ATRs)
MIN_STOP_ATR = {"swing": 0.5, "day": 1.0}
MIN_DAY_MINUTES_LEFT = 15
# Words no tool prints (test_repo checks the scripts and SKILL.md files).
SIGNAL_BANNED = ("strong buy", "strong sell", "buy now", "sell now", "you should buy",
                 "you should sell", "sure thing", "guaranteed", "can't lose", "cannot lose",
                 "winning trade", "easy money", "recommend")


def _setup(name, side, style, words, min_bars, listed, stop):
    """One row of SETUPS as a dict."""
    return {"name": name, "side": side, "style": style, "words": words,
            "min_bars": min_bars, "listed": listed, "stop": stop}


# Each setup: name, side, style, words, min_bars, listed ("near" or "pattern") and the
# stop the rule "setup" gives. In this order (the scanner ranks ties by it).
SETUPS = (
    _setup("breakout_20d", "buy", "swing", "20-day high breakout", 60, "near", "atr:2"),
    _setup("breakdown_20d", "sell", "swing", "20-day low breakdown", 60, "near", "atr:2"),
    _setup("breakout_55d", "buy", "swing", "55-day high breakout", 60, "near", "atr:2"),
    _setup("breakdown_55d", "sell", "swing", "55-day low breakdown", 60, "near", "atr:2"),
    _setup("pullback_ema20", "buy", "swing", "pullback to a rising 20-day EMA", 100,
           "pattern", "swing-low"),
    _setup("rally_ema20", "sell", "swing", "rally to a falling 20-day EMA", 100,
           "pattern", "swing-high"),
    _setup("bull_flag", "buy", "swing", "bull flag (ATR rule)", 60, "pattern", "flag-far"),
    _setup("bear_flag", "sell", "swing", "bear flag (ATR rule)", 60, "pattern", "flag-far"),
    _setup("reclaim_ma20", "buy", "swing", "back over the 20-day average", 60,
           "pattern", "swing-low"),
    _setup("lose_ma20", "sell", "swing", "back under the 20-day average", 60,
           "pattern", "swing-high"),
    _setup("oversold_bounce", "buy", "swing", "oversold bounce (RSI 14 was under 30)", 60,
           "pattern", "swing-low"),
    _setup("overbought_drop", "sell", "swing", "overbought drop (RSI 14 was over 70)", 60,
           "pattern", "swing-high"),
    _setup("inside_break_up", "buy", "swing", "inside day and NR7, upper side", 60,
           "pattern", "bar-far"),
    _setup("inside_break_down", "sell", "swing", "inside day and NR7, lower side", 60,
           "pattern", "bar-far"),
    _setup("or_breakout", "buy", "day",
           "opening-range breakout (a close above the range high)", 0, "near", "or-mid"),
    _setup("or_breakdown", "sell", "day",
           "opening-range breakdown (a close below the range low)", 0, "near", "or-mid"),
)


def _flag_level(B, A, side):
    """The bull (buy) or bear (sell) flag test on usable bars B (05 2.2): the flag top
    (buy) or bottom (sell) of B[-5:], or None when the pattern does not hold."""
    if len(B) < 16:
        return None
    pole_end = B[-6]["close"]
    flag = B[-5:]
    top = max(b["high"] for b in flag)
    bottom = min(b["low"] for b in flag)
    if top - bottom > 1.5 * A:
        return None
    if side == "buy":
        pole_low = lowest(B[-16:-6], 10, "low")
        if pole_end - pole_low >= 3 * A and bottom > (pole_low + pole_end) / 2.0:
            return top
        return None
    pole_high = highest(B[-16:-6], 10, "high")
    if pole_high - pole_end >= 3 * A and top < (pole_high + pole_end) / 2.0:
        return bottom
    return None


def _swing_level(name, B, A):
    """The level of the swing setup `name` on usable daily bars B (B[-1] is the newest
    bar), or None when its rule does not hold (05 2.2)."""
    last, prev = B[-1], B[-2]
    before = B[:-1]
    closes = [b["close"] for b in B]
    if name in ("breakout_20d", "breakout_55d"):
        return highest(before, 20 if name == "breakout_20d" else 55, "high")
    if name in ("breakdown_20d", "breakdown_55d"):
        return lowest(before, 20 if name == "breakdown_20d" else 55, "low")
    if name in ("pullback_ema20", "rally_ema20"):
        e20s, e50 = ema_series(closes, 20), ema(closes, 50)
        e20, e20_5 = e20s[-1], e20s[-6]
        if None in (e20, e20_5, e50):
            return None
        if name == "pullback_ema20":
            ok = e20 > e50 and e20 > e20_5 and last["low"] <= e20 < last["close"]
            return last["high"] if ok else None
        ok = e20 < e50 and e20 < e20_5 and last["high"] >= e20 > last["close"]
        return last["low"] if ok else None
    if name in ("bull_flag", "bear_flag"):
        return _flag_level(B, A, "buy" if name == "bull_flag" else "sell")
    if name in ("reclaim_ma20", "lose_ma20"):
        s20p, s20, s50 = sma(closes[:-1], 20), sma(closes, 20), sma(closes, 50)
        if None in (s20p, s20, s50):
            return None
        if name == "reclaim_ma20":
            ok = prev["close"] < s20p and last["close"] > s20 and last["close"] > s50
        else:
            ok = prev["close"] > s20p and last["close"] < s20 and last["close"] < s50
        return s20 if ok else None
    if name in ("oversold_bounce", "overbought_drop"):
        r = rsi_series(closes, 14)[-2]
        if r is None:
            return None
        if name == "oversold_bounce":
            return last["high"] if r < 30 and last["close"] > prev["close"] else None
        return last["low"] if r > 70 and last["close"] < prev["close"] else None
    if name in ("inside_break_up", "inside_break_down"):
        if len(B) < 7:
            return None
        inside = last["high"] < prev["high"] and last["low"] > prev["low"]
        rng = last["high"] - last["low"]
        nr7 = all(rng <= b["high"] - b["low"] for b in B[-7:])
        if not (inside and nr7):
            return None
        return last["high"] if name == "inside_break_up" else last["low"]
    return None


def coarse_words(inst):
    """The coarse-data note for a key in COARSE (02 6), or None: EURUSD moves in steps
    of about 1.25 pips; GBPUSD, AUDUSD and NZDUSD show about one price a minute."""
    key = inst.get("key")
    if key not in COARSE:
        return None
    if key == "EURUSD":
        return "Coarse data: Yahoo EURUSD moves in steps of about 1.25 pips."
    return "Coarse data: Yahoo %s shows about one price a minute." % key


def find_setups(daily_bars, inst, cfg, sides):
    """The swing hits on the newest daily bar (05 2.3), in SETUPS order: each with its
    level, last close, daily ATR, distance_atr and notes. ValueError when it cannot scan."""
    name = inst.get("name") or inst["key"]
    bars = [b for b in daily_bars if "forming" not in (b.get("flags") or ())]   # (check)
    if not bars:
        raise ValueError("No daily prices for %s yet." % name)
    bad = [f for f in SKIP_FLAGS if f in (bars[-1].get("flags") or ())]
    if bad:
        raise ValueError("The newest %s day (%s) is not usable on Yahoo (%s). No setups today."
                         % (name, day_label(parse_fx_date(bars[-1]["fx_day"])), bad[0]))
    B = usable_bars(bars)
    if len(B) < MIN_DAILY_BARS:
        raise ValueError("Only %d usable daily bars for %s (need %d)."
                         % (len(B), name, MIN_DAILY_BARS))
    A = atr_wilder(B[-ATR_WINDOW["1d"]:], 14)
    if A is None:
        raise ValueError("Not enough bars for the ATR of %s." % name)
    last = B[-1]
    hits = []
    for s in SETUPS:
        if s["style"] != "swing" or s["side"] not in sides or len(B) < s["min_bars"]:
            continue
        raw = _swing_level(s["name"], B, A)
        if raw is None:
            continue
        level = round_price(raw, inst)
        gap = level - last["close"] if s["side"] == "buy" else last["close"] - level
        dist = round(gap / A, 2)
        if s["listed"] == "near" and dist > NEAR_ATR:
            continue
        notes = []
        if dist < -CHASE_ATR:
            notes.append("The close is already %.2f ATR past the level. "
                         "A late entry is a different trade." % -dist)
        if s["name"] in ("oversold_bounce", "overbought_drop"):
            notes.append("This trades against the trend.")
        coarse = coarse_words(inst)
        if coarse:
            notes.append(coarse)
        hits.append({"symbol": inst["key"], "name": name, "side": s["side"],
                     "style": "swing", "setup": s["name"], "level": level,
                     "last": last["close"], "atr": round(A, int(inst["digits"]) + 1),
                     "atr_interval": "1d", "distance_atr": dist, "opening_range": None,
                     "data_age_min": None, "notes": notes, "fx_day": last["fx_day"]})
    return hits


def opening_range(bars_5m, session, fx_date, minutes, inst):
    """The opening range of a session on fx_date (05 3.1): high, low, mid (to the tick of
    inst, half up) and how many of the expected 5m bars were there (roll bars left out).
    None when no range bar."""
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes % 5:
        raise ValueError("The opening range must be a whole number of 5-minute bars "
                         "(got %s minutes)." % (minutes,))
    start, end = opening_range_window(session, fx_date, minutes)
    rng = [b for b in bars_5m if start <= b["start"] < end
           and "roll" not in (b.get("flags") or ())]
    if not rng:
        return None   # no range bar at all (a range of roll bars only: None too, check)
    high = max(b["high"] for b in rng)
    low = min(b["low"] for b in rng)
    mid = round_price((high + low) / 2.0, inst)
    return {"session": session, "start": utc_iso(start), "end": utc_iso(end),
            "high": high, "low": low, "mid": mid, "bars": len(rng), "of": minutes // 5}


def or_trigger(bars_5m, or_, side, after_utc, until_utc):
    """The first finished 5m bar after the range (and after after_utc) whose close is
    beyond it: buy above the high, sell below the low; done by until_utc. Or None."""
    if or_["bars"] < or_["of"]:
        raise ValueError("The opening range is not complete.")
    if side not in ("buy", "sell"):
        raise ValueError('A side is "buy" or "sell", not "%s".' % (side,))
    frm = parse_utc(or_["end"])
    if after_utc is not None and after_utc > frm:
        frm = after_utc
    step = datetime.timedelta(minutes=5)
    for b in sorted(bars_5m, key=lambda x: x["start"]):
        if b["start"] < frm or b["start"] + step > until_utc:
            continue
        if "roll" in (b.get("flags") or ()) or b.get("after_gap"):   # every after_gap: the safe side
            continue
        if side == "buy" and b["close"] > or_["high"]:
            return b
        if side == "sell" and b["close"] < or_["low"]:
            return b
    return None


# ---- correlation groups and currency legs (part 05 sections 5.5 and 5.6)

# group name -> the keys in it (part 01's first cut, kept). A key in no group is a group
# of one. The instrument's own "group" column wins over this table.
CORRELATION_GROUPS = {
    "usd_majors": ("EURUSD", "GBPUSD", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD"),
    "jpy": ("USDJPY", "EURJPY", "GBPJPY", "AUDJPY"),
    "eur_crosses": ("EURGBP", "EURCHF"),
    "metals": ("XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD"),
    "industrial": ("COPPER",),
    "energy": ("USOIL", "UKOIL"),   # WTI and Brent: one oil market (check: not measured)
    "natgas": ("NATGAS",),
}


def group_of(symbol, cfg):
    """The correlation group of a key: its "group" column when set, else the group of
    CORRELATION_GROUPS that holds it, else the key itself (a group of one)."""
    row = ((cfg or {}).get("instruments") or {}).get(symbol) or {}
    if row.get("group"):
        return row["group"]
    for name, keys in CORRELATION_GROUPS.items():
        if symbol in keys:
            return name
    return symbol


def currency_legs(symbol, side, inst):
    """The currency bets of one trade, +1 long and -1 short: fx {base: s, quote: -s},
    metal {base: s, "USD": -s}, energy {base: s} only; s = 1 for a buy, -1 for a sell."""
    if side not in ("buy", "sell"):
        raise ValueError('A side is "buy" or "sell", not "%s".' % (side,))
    s = 1 if side == "buy" else -1
    kind = inst.get("kind")
    if kind == "fx":
        return {inst["base"]: s, inst["quote"]: -s}
    if kind == "metal":
        return {inst["base"]: s, "USD": -s}
    if kind == "energy":
        return {inst["base"]: s}
    raise ValueError('%s: kind must be "fx", "metal" or "energy", not "%s".' % (symbol, kind))


# ---------------------------------------------------------------- files and config
# group: files and config

# ---- config.json (part 01 sections 3 and 4)

CONFIG_NOTE = "No fx/config.json yet. Using the example values. Copy your own from MetaTrader."
SECRET_KEYS = ("password", "login", "investor_password", "master_password", "account_number")
MERGED_KEYS = ("account", "risk", "swing", "day", "news")   # merged one level deep; the rest replaced whole
D15_DAYS = (("2026-01-14", "US winter"), ("2026-07-15", "US summer"))   # the two test FX days of 01 3.3
KINDS = ("fx", "metal", "energy")
PRICE_SOURCES = ("spot_fx", "futures")
CALC_MODES = ("forex", "forex_no_leverage", "cfd", "cfd_leverage", "futures")
SWAP_MODES = ("points", "deposit", "base", "margin", "profit", "interest", "disabled")
TRIPLE_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "none")
PROFIT_CURRENCIES = ("USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD")   # the rates the cache has
FUTURES_KEYS = ("root", "suffix", "cycle", "roll_rule", "settle", "shortname_month", "history_check")
FUTURES_SUFFIXES = (".CMX", ".NYM")
FUTURES_MONTH_LETTERS = "".join(MONTH_CODES.values())   # FGHJKMNQUVXZ, Jan to Dec (part 02)
ROLL_RULES = ("gc_fnd", "si_hg", "cl_ltd", "bz_ltd", "ng_ltd")   # part 02 section 5 (check)
SWING_STOP_RULES = ("swing-low", "swing-high", "flag-far", "bar-far", "setup")   # part 05 8
DAY_STOP_RULES = ("or-mid", "or-far", "setup")
SESSION_WORDS = {"london": "London", "new_york": "New York", "tokyo": "Tokyo"}
NEEDED_COLUMNS = ("name", "kind", "yahoo", "price_source", "base", "quote", "profit_currency",
                  "margin_currency", "contract_size", "digits", "min_lot", "max_lot", "lot_step",
                  "calc_mode", "spread_points", "swap_mode", "swap_long", "swap_short",
                  "swap_triple_day", "hours", "data_hours", "group")   # plus futures for a futures row
COLUMN_DEFAULTS = {"pip": None, "margin_rate": 1.0, "leverage": None, "initial_margin": None,
                   "commission_per_lot_side": 0, "swap_free_fee_per_lot": None,
                   "swap_free_grace_nights": 0, "stops_level_points": 0, "yahoo_to_broker": 1.0,
                   "futures": None}   # a new key's columns that are not needed (01 4.1)


BUILTIN_INSTRUMENTS = {
    "EURUSD": {
        "name": "Euro / US dollar", "kind": "fx", "yahoo": "EURUSD=X", "price_source": "spot_fx",
        "base": "EUR", "quote": "USD", "pip": 0.0001, "hours": "fx", "data_hours": "yahoo_fx",
        "group": "usd_majors", "broker_symbol": "EURUSD", "contract_size": 100000, "digits": 5,
        "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01, "calc_mode": "forex", "margin_rate": 1.0,
        "leverage": None, "initial_margin": None, "spread_points": 1,
        "commission_per_lot_side": 3.0, "swap_mode": "points", "swap_long": -6.23,
        "swap_short": 3.1, "swap_triple_day": "wednesday", "swap_free_fee_per_lot": None,
        "swap_free_grace_nights": 0, "stops_level_points": 0, "profit_currency": "USD",
        "margin_currency": "EUR", "yahoo_to_broker": 1.0,
        "futures": None},
    "GBPUSD": {
        "name": "British pound / US dollar", "kind": "fx", "yahoo": "GBPUSD=X",
        "price_source": "spot_fx", "base": "GBP", "quote": "USD", "pip": 0.0001, "hours": "fx",
        "data_hours": "yahoo_fx", "group": "usd_majors", "broker_symbol": "GBPUSD",
        "contract_size": 100000, "digits": 5, "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01,
        "calc_mode": "forex", "margin_rate": 1.0, "leverage": None, "initial_margin": None,
        "spread_points": 3, "commission_per_lot_side": 3.0, "swap_mode": "points",
        "swap_long": -1.63, "swap_short": -1.37, "swap_triple_day": "wednesday",
        "swap_free_fee_per_lot": None, "swap_free_grace_nights": 0, "stops_level_points": 0,
        "profit_currency": "USD", "margin_currency": "GBP", "yahoo_to_broker": 1.0,
        "futures": None},
    "USDJPY": {
        "name": "US dollar / Japanese yen", "kind": "fx", "yahoo": "USDJPY=X",
        "price_source": "spot_fx", "base": "USD", "quote": "JPY", "pip": 0.01, "hours": "fx",
        "data_hours": "yahoo_fx", "group": "jpy", "broker_symbol": "USDJPY",
        "contract_size": 100000, "digits": 3, "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01,
        "calc_mode": "forex", "margin_rate": 1.0, "leverage": None, "initial_margin": None,
        "spread_points": 1, "commission_per_lot_side": 3.0, "swap_mode": "points", "swap_long": 6.9,
        "swap_short": -13.93, "swap_triple_day": "wednesday", "swap_free_fee_per_lot": 10,
        "swap_free_grace_nights": 6, "stops_level_points": 0, "profit_currency": "JPY",
        "margin_currency": "USD", "yahoo_to_broker": 1.0,
        "futures": None},
    "USDCHF": {
        "name": "US dollar / Swiss franc", "kind": "fx", "yahoo": "USDCHF=X",
        "price_source": "spot_fx", "base": "USD", "quote": "CHF", "pip": 0.0001, "hours": "fx",
        "data_hours": "yahoo_fx", "group": "usd_majors", "broker_symbol": "USDCHF",
        "contract_size": 100000, "digits": 5, "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01,
        "calc_mode": "forex", "margin_rate": 1.0, "leverage": None, "initial_margin": None,
        "spread_points": 4, "commission_per_lot_side": 3.0, "swap_mode": "points",
        "swap_long": 6.23, "swap_short": -10.07, "swap_triple_day": "wednesday",
        "swap_free_fee_per_lot": 10, "swap_free_grace_nights": 6, "stops_level_points": 0,
        "profit_currency": "CHF", "margin_currency": "USD", "yahoo_to_broker": 1.0,
        "futures": None},
    "AUDUSD": {
        "name": "Australian dollar / US dollar", "kind": "fx", "yahoo": "AUDUSD=X",
        "price_source": "spot_fx", "base": "AUD", "quote": "USD", "pip": 0.0001, "hours": "fx",
        "data_hours": "yahoo_fx", "group": "usd_majors", "broker_symbol": "AUDUSD",
        "contract_size": 100000, "digits": 5, "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01,
        "calc_mode": "forex", "margin_rate": 1.0, "leverage": None, "initial_margin": None,
        "spread_points": 1, "commission_per_lot_side": 3.0, "swap_mode": "points", "swap_long": 0.7,
        "swap_short": -2.3, "swap_triple_day": "wednesday", "swap_free_fee_per_lot": None,
        "swap_free_grace_nights": 0, "stops_level_points": 0, "profit_currency": "USD",
        "margin_currency": "AUD", "yahoo_to_broker": 1.0,
        "futures": None},
    "USDCAD": {
        "name": "US dollar / Canadian dollar", "kind": "fx", "yahoo": "USDCAD=X",
        "price_source": "spot_fx", "base": "USD", "quote": "CAD", "pip": 0.0001, "hours": "fx",
        "data_hours": "yahoo_fx", "group": "usd_majors", "broker_symbol": "USDCAD",
        "contract_size": 100000, "digits": 5, "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01,
        "calc_mode": "forex", "margin_rate": 1.0, "leverage": None, "initial_margin": None,
        "spread_points": 2, "commission_per_lot_side": 3.0, "swap_mode": "points", "swap_long": 4.4,
        "swap_short": -7.5, "swap_triple_day": "wednesday", "swap_free_fee_per_lot": None,
        "swap_free_grace_nights": 0, "stops_level_points": 0, "profit_currency": "CAD",
        "margin_currency": "USD", "yahoo_to_broker": 1.0,
        "futures": None},
    "NZDUSD": {
        "name": "New Zealand dollar / US dollar", "kind": "fx", "yahoo": "NZDUSD=X",
        "price_source": "spot_fx", "base": "NZD", "quote": "USD", "pip": 0.0001, "hours": "fx",
        "data_hours": "yahoo_fx", "group": "usd_majors", "broker_symbol": "NZDUSD",
        "contract_size": 100000, "digits": 5, "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01,
        "calc_mode": "forex", "margin_rate": 1.0, "leverage": None, "initial_margin": None,
        "spread_points": 3, "commission_per_lot_side": 3.0, "swap_mode": "points",
        "swap_long": -3.12, "swap_short": 1.37, "swap_triple_day": "wednesday",
        "swap_free_fee_per_lot": None, "swap_free_grace_nights": 0, "stops_level_points": 0,
        "profit_currency": "USD", "margin_currency": "NZD", "yahoo_to_broker": 1.0,
        "futures": None},
    "EURJPY": {
        "name": "Euro / Japanese yen", "kind": "fx", "yahoo": "EURJPY=X", "price_source": "spot_fx",
        "base": "EUR", "quote": "JPY", "pip": 0.01, "hours": "fx", "data_hours": "yahoo_fx",
        "group": "jpy", "broker_symbol": "EURJPY", "contract_size": 100000, "digits": 3,
        "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01, "calc_mode": "forex", "margin_rate": 1.0,
        "leverage": None, "initial_margin": None, "spread_points": 5,
        "commission_per_lot_side": 3.0, "swap_mode": "points", "swap_long": 4.56,
        "swap_short": -9.21, "swap_triple_day": "wednesday", "swap_free_fee_per_lot": 5,
        "swap_free_grace_nights": 6, "stops_level_points": 0, "profit_currency": "JPY",
        "margin_currency": "EUR", "yahoo_to_broker": 1.0,
        "futures": None},
    "GBPJPY": {
        "name": "British pound / Japanese yen", "kind": "fx", "yahoo": "GBPJPY=X",
        "price_source": "spot_fx", "base": "GBP", "quote": "JPY", "pip": 0.01, "hours": "fx",
        "data_hours": "yahoo_fx", "group": "jpy", "broker_symbol": "GBPJPY",
        "contract_size": 100000, "digits": 3, "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01,
        "calc_mode": "forex", "margin_rate": 1.0, "leverage": None, "initial_margin": None,
        "spread_points": 10, "commission_per_lot_side": 3.0, "swap_mode": "points",
        "swap_long": 9.07, "swap_short": -21.8, "swap_triple_day": "wednesday",
        "swap_free_fee_per_lot": 10, "swap_free_grace_nights": 6, "stops_level_points": 0,
        "profit_currency": "JPY", "margin_currency": "GBP", "yahoo_to_broker": 1.0,
        "futures": None},
    "EURGBP": {
        "name": "Euro / British pound", "kind": "fx", "yahoo": "EURGBP=X",
        "price_source": "spot_fx", "base": "EUR", "quote": "GBP", "pip": 0.0001, "hours": "fx",
        "data_hours": "yahoo_fx", "group": "eur_crosses", "broker_symbol": "EURGBP",
        "contract_size": 100000, "digits": 5, "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01,
        "calc_mode": "forex", "margin_rate": 1.0, "leverage": None, "initial_margin": None,
        "spread_points": 4, "commission_per_lot_side": 3.0, "swap_mode": "points",
        "swap_long": -4.31, "swap_short": 2.12, "swap_triple_day": "wednesday",
        "swap_free_fee_per_lot": None, "swap_free_grace_nights": 0, "stops_level_points": 0,
        "profit_currency": "GBP", "margin_currency": "EUR", "yahoo_to_broker": 1.0,
        "futures": None},
    "AUDJPY": {
        "name": "Australian dollar / Japanese yen", "kind": "fx", "yahoo": "AUDJPY=X",
        "price_source": "spot_fx", "base": "AUD", "quote": "JPY", "pip": 0.01, "hours": "fx",
        "data_hours": "yahoo_fx", "group": "jpy", "broker_symbol": "AUDJPY",
        "contract_size": 100000, "digits": 3, "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01,
        "calc_mode": "forex", "margin_rate": 1.0, "leverage": None, "initial_margin": None,
        "spread_points": 9, "commission_per_lot_side": 3.0, "swap_mode": "points",
        "swap_long": 6.58, "swap_short": -12.78, "swap_triple_day": "wednesday",
        "swap_free_fee_per_lot": 5, "swap_free_grace_nights": 6, "stops_level_points": 0,
        "profit_currency": "JPY", "margin_currency": "AUD", "yahoo_to_broker": 1.0,
        "futures": None},
    "EURCHF": {
        "name": "Euro / Swiss franc", "kind": "fx", "yahoo": "EURCHF=X", "price_source": "spot_fx",
        "base": "EUR", "quote": "CHF", "pip": 0.0001, "hours": "fx", "data_hours": "yahoo_fx",
        "group": "eur_crosses", "broker_symbol": "EURCHF", "contract_size": 100000, "digits": 5,
        "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01, "calc_mode": "forex", "margin_rate": 1.0,
        "leverage": None, "initial_margin": None, "spread_points": 9,
        "commission_per_lot_side": 3.0, "swap_mode": "points", "swap_long": 4.43,
        "swap_short": -9.99, "swap_triple_day": "wednesday", "swap_free_fee_per_lot": 10,
        "swap_free_grace_nights": 6, "stops_level_points": 0, "profit_currency": "CHF",
        "margin_currency": "EUR", "yahoo_to_broker": 1.0,
        "futures": None},
    "XAUUSD": {
        "name": "Gold", "kind": "metal", "yahoo": "GC=F", "price_source": "futures", "base": "XAU",
        "quote": "USD", "pip": None, "hours": "comex", "data_hours": "cme", "group": "metals",
        "broker_symbol": "XAUUSD", "contract_size": 100, "digits": 2, "min_lot": 0.01,
        "max_lot": 100, "lot_step": 0.01, "calc_mode": "cfd_leverage", "margin_rate": 1.0,
        "leverage": None, "initial_margin": None, "spread_points": 9,
        "commission_per_lot_side": 3.0, "swap_mode": "points", "swap_long": -54.84,
        "swap_short": 38.52, "swap_triple_day": "wednesday", "swap_free_fee_per_lot": 45,
        "swap_free_grace_nights": 6, "stops_level_points": 0, "profit_currency": "USD",
        "margin_currency": "USD", "yahoo_to_broker": 1.0,
        "futures": {"root": "GC", "suffix": ".CMX", "cycle": "GJMQZ",
                    "roll_rule": "gc_fnd", "settle": ["ny", "13:30"],
                    "shortname_month": True, "history_check": True}},
    "XAGUSD": {
        "name": "Silver", "kind": "metal", "yahoo": "SI=F", "price_source": "futures",
        "base": "XAG", "quote": "USD", "pip": None, "hours": "comex", "data_hours": "cme",
        "group": "metals", "broker_symbol": "XAGUSD", "contract_size": 5000, "digits": 3,
        "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01, "calc_mode": "cfd_leverage",
        "margin_rate": 1.0, "leverage": None, "initial_margin": None, "spread_points": 21,
        "commission_per_lot_side": 3.0, "swap_mode": "points", "swap_long": -8.21,
        "swap_short": 3.99, "swap_triple_day": "wednesday", "swap_free_fee_per_lot": 15,
        "swap_free_grace_nights": 6, "stops_level_points": 0, "profit_currency": "USD",
        "margin_currency": "USD", "yahoo_to_broker": 1.0,
        "futures": {"root": "SI", "suffix": ".CMX", "cycle": "HKNUZ",
                    "roll_rule": "si_hg", "settle": ["ny", "13:25"],
                    "shortname_month": True, "history_check": False}},
    "XPTUSD": {
        "name": "Platinum", "kind": "metal", "yahoo": "PL=F", "price_source": "futures",
        "base": "XPT", "quote": "USD", "pip": None, "hours": "nymex", "data_hours": "cme",
        "group": "metals", "broker_symbol": "XPTUSD", "contract_size": 100, "digits": 2,
        "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01, "calc_mode": "cfd_leverage",
        "margin_rate": 1.0, "leverage": None, "initial_margin": None, "spread_points": 420,
        "commission_per_lot_side": 3.0, "swap_mode": "interest", "swap_long": -4.78,
        "swap_short": 3.36, "swap_triple_day": "wednesday", "swap_free_fee_per_lot": None,
        "swap_free_grace_nights": 0, "stops_level_points": 0, "profit_currency": "USD",
        "margin_currency": "USD", "yahoo_to_broker": 1.0,
        "futures": {"root": "PL", "suffix": ".NYM", "cycle": "FJNV",
                    "roll_rule": "gc_fnd", "settle": ["ny", "13:05"],
                    "shortname_month": True, "history_check": True}},
    "XPDUSD": {
        "name": "Palladium", "kind": "metal", "yahoo": "PA=F", "price_source": "futures",
        "base": "XPD", "quote": "USD", "pip": None, "hours": "nymex", "data_hours": "cme",
        "group": "metals", "broker_symbol": "XPDUSD", "contract_size": 100, "digits": 2,
        "min_lot": 0.01, "max_lot": 100, "lot_step": 0.01, "calc_mode": "cfd_leverage",
        "margin_rate": 1.0, "leverage": None, "initial_margin": None, "spread_points": 1640,
        "commission_per_lot_side": 3.0, "swap_mode": "interest", "swap_long": -4.78,
        "swap_short": 3.36, "swap_triple_day": "wednesday", "swap_free_fee_per_lot": None,
        "swap_free_grace_nights": 0, "stops_level_points": 0, "profit_currency": "USD",
        "margin_currency": "USD", "yahoo_to_broker": 1.0,
        "futures": {"root": "PA", "suffix": ".NYM", "cycle": "HMUZ",
                    "roll_rule": "gc_fnd", "settle": ["ny", "13:00"],
                    "shortname_month": True, "history_check": True}},
    "COPPER": {
        "name": "Copper", "kind": "metal", "yahoo": "HG=F", "price_source": "futures",
        "base": "XCU", "quote": "USD", "pip": None, "hours": "comex", "data_hours": "cme",
        "group": "industrial", "broker_symbol": "COPPER", "contract_size": 1000, "digits": 4,
        "min_lot": 1, "max_lot": 250, "lot_step": 1, "calc_mode": "cfd_leverage",
        "margin_rate": 1.0, "leverage": 50, "initial_margin": None, "spread_points": 21,
        "commission_per_lot_side": 0.0, "swap_mode": "points", "swap_long": -16.03,
        "swap_short": 6.19, "swap_triple_day": "friday", "swap_free_fee_per_lot": 1,
        "swap_free_grace_nights": 6, "stops_level_points": 0, "profit_currency": "USD",
        "margin_currency": "USD", "yahoo_to_broker": 1.0,
        "futures": {"root": "HG", "suffix": ".CMX", "cycle": "HKNUZ",
                    "roll_rule": "si_hg", "settle": ["ny", "13:00"],
                    "shortname_month": True, "history_check": False}},
    "USOIL": {
        "name": "WTI crude oil", "kind": "energy", "yahoo": "CL=F", "price_source": "futures",
        "base": "WTI", "quote": "USD", "pip": None, "hours": "nymex", "data_hours": "cme",
        "group": "energy", "broker_symbol": "USOIL", "contract_size": 1, "digits": 2, "min_lot": 1,
        "max_lot": 10000, "lot_step": 1, "calc_mode": "cfd_leverage", "margin_rate": 1.0,
        "leverage": None, "initial_margin": None, "spread_points": 4,
        "commission_per_lot_side": 0.0, "swap_mode": "points", "swap_long": 0.78,
        "swap_short": -1.39, "swap_triple_day": "friday", "swap_free_fee_per_lot": 0.1,
        "swap_free_grace_nights": 3, "stops_level_points": 0, "profit_currency": "USD",
        "margin_currency": "USD", "yahoo_to_broker": 1.0,
        "futures": {"root": "CL", "suffix": ".NYM", "cycle": "FGHJKMNQUVXZ",
                    "roll_rule": "cl_ltd", "settle": ["ny", "14:30"],
                    "shortname_month": True, "history_check": True}},
    "UKOIL": {
        "name": "Brent crude oil", "kind": "energy", "yahoo": "BZ=F", "price_source": "futures",
        "base": "BRENT", "quote": "USD", "pip": None, "hours": "ice_brent", "data_hours": "cme",
        "group": "energy", "broker_symbol": "UKOIL", "contract_size": 1, "digits": 2, "min_lot": 1,
        "max_lot": 10000, "lot_step": 1, "calc_mode": "cfd_leverage", "margin_rate": 1.0,
        "leverage": None, "initial_margin": None, "spread_points": 4,
        "commission_per_lot_side": 0.0, "swap_mode": "points", "swap_long": 2.27,
        "swap_short": -3.17, "swap_triple_day": "friday", "swap_free_fee_per_lot": 0.1,
        "swap_free_grace_nights": 3, "stops_level_points": 0, "profit_currency": "USD",
        "margin_currency": "USD", "yahoo_to_broker": 1.0,
        "futures": {"root": "BZ", "suffix": ".NYM", "cycle": "FGHJKMNQUVXZ",
                    "roll_rule": "bz_ltd", "settle": ["london", "19:30"],
                    "shortname_month": False, "history_check": True}},
    "NATGAS": {
        "name": "Natural gas", "kind": "energy", "yahoo": "NG=F", "price_source": "futures",
        "base": "NG", "quote": "USD", "pip": None, "hours": "nymex", "data_hours": "cme",
        "group": "natgas", "broker_symbol": "NATGAS", "contract_size": 10000, "digits": 3,
        "min_lot": 0.01, "max_lot": 50, "lot_step": 0.01, "calc_mode": "cfd_leverage",
        "margin_rate": 1.0, "leverage": 20, "initial_margin": None, "spread_points": 4,
        "commission_per_lot_side": 0.0, "swap_mode": "points", "swap_long": -8.99,
        "swap_short": 5.31, "swap_triple_day": "friday", "swap_free_fee_per_lot": 80,
        "swap_free_grace_nights": 0, "stops_level_points": 0, "profit_currency": "USD",
        "margin_currency": "USD", "yahoo_to_broker": 1.0,
        "futures": {"root": "NG", "suffix": ".NYM", "cycle": "FGHJKMNQUVXZ",
                    "roll_rule": "ng_ltd", "settle": ["ny", "14:30"],
                    "shortname_month": True, "history_check": True}},
}

DEFAULTS = {
    "account": {"currency": "USD", "balance": 1000, "leverage": 100, "margin_call_pct": 100,
                "stop_out_pct": 50, "swap_free": False, "server_time": "ny+7", "platform": "mt5",
                "hedging": True},
    "paper": True,
    "yahoo": True,
    "allow_short": True,
    "style": "swing",
    "day_end": "17:00 New York",
    "universe": list(BUILTIN_INSTRUMENTS),
    "day_universe": ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"],
    "risk": {"max_risk_per_trade_pct": 1.0, "max_risk_per_trade_usd": None,
             "max_daily_loss_pct": 3.0, "max_daily_loss_usd": None, "max_open_trades": 3,
             "max_trades_per_day": 3, "max_orders_per_day": 10, "max_group_risk_pct": 2.0,
             "max_currency_risk_pct": 2.0, "min_reward_risk": 1.5, "require_stop": True,
             "max_margin_used_pct": 30, "min_margin_level_pct": 300, "blocked": []},
    "swing": {"stop": "atr:2", "target": "2R", "entry": "market", "max_hold_days": 20,
              "friday_cutoff_ny": "12:00"},
    "day": {"or_session": "london", "or_minutes": 15, "stop": "or-mid", "target": "2R",
            "no_entries_after": "21:00", "flat_by": "23:30"},
    "news": {"quiet_before_min": 30, "quiet_after_min": 30, "impacts": ["High"], "feeds": [
        {"name": "fxstreet", "url": "https://www.fxstreet.com/rss/news"},
        {"name": "investinglive", "url": "https://investinglive.com/feed/"},
        {"name": "investing-commodities", "url": "https://www.investing.com/rss/news_11.rss"},
        {"name": "investing-forex", "url": "https://www.investing.com/rss/news_1.rss"},
        {"name": "oilprice", "url": "https://oilprice.com/rss/main"},
        {"name": "cnbc-energy",
         "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19836768"},
        {"name": "fed-monetary", "url": "https://www.federalreserve.gov/feeds/press_monetary.xml"},
        {"name": "ecb-press", "url": "https://www.ecb.europa.eu/rss/press.html"},
        {"name": "boe-news", "url": "https://www.bankofengland.co.uk/rss/news"},
        {"name": "boj-news", "url": "https://www.boj.or.jp/en/rss/whatsnew.xml"}]},
    "calendar_url": "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "holidays": {},
    "early_closes": {},
    "fx_rates_max_age_hours": 24,
    "instruments": BUILTIN_INSTRUMENTS,
}


def _cfg_get(cfg, key):
    """The value at a dotted key ("risk.max_open_trades"); None when a part is missing."""
    v = cfg
    for part in key.split("."):
        v = v.get(part) if isinstance(v, dict) else None
    return v


def _cfg_put(cfg, key, value):
    """Set the value at a dotted key (the parents must exist)."""
    parts = key.split(".")
    for part in parts[:-1]:
        cfg = cfg[part]
    cfg[parts[-1]] = value


def _is_number(x):
    """True for a JSON int or float that is finite; never for a bool."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _num_text(x):
    """A range end for a message: 100000000 -> "100,000,000"; 0.01 -> "0.01"."""
    if float(x) == int(x):
        return "{:,}".format(int(x))
    return "%g" % x


def _is_hhmm(text):
    """True for "HH:MM": two digits, a colon, two digits, 00:00 to 23:59."""
    return isinstance(text, str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", text) is not None


def _cfg_bad(path, key, want, value):
    """The generic message: <path>: "<key>" must be <want> (you have <value>)."""
    return ValueError('%s: "%s" must be %s (you have %s)' % (path, key, want, json.dumps(value)))


def _cfg_bool(cfg, key, path):
    """The value at key, which must be JSON true or false."""
    v = _cfg_get(cfg, key)
    if not isinstance(v, bool):
        raise _cfg_bad(path, key, "true or false", v)
    return v


def _cfg_number(cfg, key, lo, hi, path, null_ok=False):
    """The value at key, a number from lo to hi (or null when null_ok)."""
    v = _cfg_get(cfg, key)
    if v is None and null_ok:
        return None
    if not _is_number(v) or not lo <= v <= hi:
        raise _cfg_bad(path, key, "a number from %s to %s" % (_num_text(lo), _num_text(hi)), v)
    return v


def _cfg_int(cfg, key, lo, hi, path):
    """The value at key, a whole number from lo to hi. 3.0 is stored back as 3."""
    v = _cfg_get(cfg, key)
    if not _is_number(v) or v != int(v) or not lo <= v <= hi:
        raise _cfg_bad(path, key, "a whole number from %s to %s" % (_num_text(lo), _num_text(hi)), v)
    _cfg_put(cfg, key, int(v))
    return int(v)


def _cfg_choice(cfg, key, choices, path):
    """The value at key, which must be one of choices."""
    v = _cfg_get(cfg, key)
    if not isinstance(v, str) or v not in choices:
        raise _cfg_bad(path, key, "one of " + ", ".join(choices), v)
    return v


def _cfg_hhmm(cfg, key, path):
    """The value at key, which must be "HH:MM"."""
    v = _cfg_get(cfg, key)
    if not _is_hhmm(v):
        raise ValueError('%s: "%s" must be a time like 23:30 (you have %s)' % (path, key, json.dumps(v)))
    return v


def _cfg_keys(cfg, key, path, at_least=1, repeats=False):
    """The list at key: instrument keys of cfg["instruments"], at least at_least of them."""
    v = _cfg_get(cfg, key)
    if not isinstance(v, list) or len(v) < at_least or not all(isinstance(x, str) for x in v):
        want = "a list of instrument keys" + (", at least %d" % at_least if at_least else "")
        raise _cfg_bad(path, key, want, v)
    seen = set()
    for sym in v:
        if sym not in cfg["instruments"]:
            raise ValueError('%s: "%s": "%s" is not in your instrument list' % (path, key, sym))
        if sym in seen and not repeats:
            raise ValueError('%s: "%s" lists "%s" twice' % (path, key, sym))
        seen.add(sym)
    return v


def _rule_k(text, form):
    """K of "atr:K" (form "atr") or "KR" (form "R") when K is 0.5 to 10; else None."""
    pat = r"atr:(\d+(?:\.\d+)?)" if form == "atr" else r"(\d+(?:\.\d+)?)R"
    m = re.fullmatch(pat, text) if isinstance(text, str) else None
    if not m or not 0.5 <= float(m.group(1)) <= 10:
        return None
    return float(m.group(1))


def _is_date(text):
    """True for a real date written "YYYY-MM-DD"."""
    if not isinstance(text, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return False
    try:
        datetime.date(int(text[:4]), int(text[5:7]), int(text[8:10]))
    except ValueError:
        return False
    return True


def _check_account(cfg, path):
    """The account.* keys of 01 3.2, in table order."""
    if _cfg_get(cfg, "account.currency") != "USD":
        raise ValueError('%s: "account.currency" must be "USD". The pack counts money in US '
                         'dollars only.' % path)
    _cfg_number(cfg, "account.balance", 1, 100000000, path)
    _cfg_number(cfg, "account.leverage", 1, 5000, path)
    call = _cfg_number(cfg, "account.margin_call_pct", 0, 1000, path)
    if _cfg_number(cfg, "account.stop_out_pct", 0, 1000, path) > call:
        raise ValueError('%s: "account.stop_out_pct" must not be above "account.margin_call_pct"' % path)
    _cfg_bool(cfg, "account.swap_free", path)
    server = _cfg_get(cfg, "account.server_time")
    try:
        if not isinstance(server, str):
            raise ValueError(SERVER_TIME_BAD)
        server_offset(server)
    except ValueError:
        raise ValueError("%s: %s" % (path, SERVER_TIME_BAD))
    platform = _cfg_choice(cfg, "account.platform", ("mt5", "mt4"), path)
    if not _cfg_bool(cfg, "account.hedging", path) and platform == "mt4":
        raise ValueError('%s: MT4 accounts always hedge. Set "account.hedging" to true.' % path)


def _check_top(cfg, path):
    """paper, yahoo, allow_short, style, day_end, universe and day_universe."""
    for key in ("paper", "yahoo", "allow_short"):
        _cfg_bool(cfg, key, path)
    _cfg_choice(cfg, "style", ("swing", "day"), path)
    _cfg_choice(cfg, "day_end", DAY_ENDS, path)
    _cfg_keys(cfg, "universe", path)
    _cfg_keys(cfg, "day_universe", path)


def _check_risk(cfg, path):
    """The risk.* keys of 01 3.2, in table order (after the account keys)."""
    below = '%s: "%s" is below the risk of one trade. No trade could pass.'
    pct = _cfg_number(cfg, "risk.max_risk_per_trade_pct", 0.01, 10, path)
    usd = _cfg_number(cfg, "risk.max_risk_per_trade_usd", 1, 1000000, path, null_ok=True)
    if _cfg_number(cfg, "risk.max_daily_loss_pct", 0.01, 30, path) < pct:
        raise ValueError(below % (path, "risk.max_daily_loss_pct"))
    daily_usd = _cfg_number(cfg, "risk.max_daily_loss_usd", 1, 1000000, path, null_ok=True)
    if daily_usd is not None and usd is not None and daily_usd < usd:
        raise ValueError(below % (path, "risk.max_daily_loss_usd"))
    _cfg_int(cfg, "risk.max_open_trades", 1, 50, path)
    per_day = _cfg_int(cfg, "risk.max_trades_per_day", 1, 100, path)
    if _cfg_int(cfg, "risk.max_orders_per_day", 1, 200, path) < per_day:
        raise ValueError('%s: "risk.max_orders_per_day" must be at least "risk.max_trades_per_day"' % path)
    _cfg_number(cfg, "risk.max_group_risk_pct", 0.01, 30, path)
    _cfg_number(cfg, "risk.max_currency_risk_pct", 0.01, 30, path)
    _cfg_number(cfg, "risk.min_reward_risk", 0, 10, path)
    _cfg_bool(cfg, "risk.require_stop", path)
    _cfg_number(cfg, "risk.max_margin_used_pct", 1, 100, path)
    if _cfg_number(cfg, "risk.min_margin_level_pct", 0, 100000, path) < cfg["account"]["margin_call_pct"]:
        raise ValueError('%s: "risk.min_margin_level_pct" must be at least the margin call level' % path)
    _cfg_keys(cfg, "risk.blocked", path, at_least=0, repeats=True)


def _cfg_target(cfg, key, path):
    """A target rule "KR" with K from 0.5 to 10 ("1.5R" is fine)."""
    v = _cfg_get(cfg, key)
    if _rule_k(v, "R") is None:
        raise _cfg_bad(path, key, "like 2R, from 0.5R to 10R", v)
    return v


def _cfg_stop(cfg, key, names, path):
    """A stop rule: "atr:K" (K from 0.5 to 10) or one of names."""
    v = _cfg_get(cfg, key)
    if _rule_k(v, "atr") is None and not (isinstance(v, str) and v in names):
        raise _cfg_bad(path, key, "atr:K (K from 0.5 to 10) or one of " + ", ".join(names), v)
    return v


def _check_swing(cfg, path):
    """The swing.* keys of 01 3.2."""
    stop = _cfg_get(cfg, "swing.stop")
    if isinstance(stop, str) and stop.startswith("price:"):
        raise ValueError('%s: "swing.stop" cannot be a fixed price. Give a price with --stop on '
                         'the command line.' % path)
    _cfg_stop(cfg, "swing.stop", SWING_STOP_RULES, path)
    _cfg_target(cfg, "swing.target", path)
    _cfg_choice(cfg, "swing.entry", ("market", "limit"), path)
    _cfg_int(cfg, "swing.max_hold_days", 1, 120, path)
    if _cfg_hhmm(cfg, "swing.friday_cutoff_ny", path) >= "17:00":
        raise ValueError('%s: "swing.friday_cutoff_ny" must be before 17:00 New York (the week '
                         'ends then)' % path)


def _check_day(cfg, path):
    """The day.* keys of 01 3.2, then the D15 check of 01 3.3."""
    _cfg_choice(cfg, "day.or_session", ("london", "new_york", "tokyo"), path)
    m = _cfg_get(cfg, "day.or_minutes")
    if _is_number(m) and m == int(m) and 5 <= m <= 120 and int(m) % 5:
        raise ValueError('%s: "day.or_minutes" must be a multiple of 5 (the bars are 5 minutes)' % path)
    _cfg_int(cfg, "day.or_minutes", 5, 120, path)
    _cfg_stop(cfg, "day.stop", DAY_STOP_RULES, path)
    _cfg_target(cfg, "day.target", path)
    _cfg_hhmm(cfg, "day.no_entries_after", path)
    _cfg_hhmm(cfg, "day.flat_by", path)
    _check_day_times(cfg, path)


def _pkt_hhmm(utc):
    """naive UTC -> "03:00 PKT" (the Karachi clock)."""
    return to_pkt(utc).strftime("%H:%M") + " PKT"


def _check_day_times(cfg, path):
    """The D15 check (01 3.3): day.flat_by and day.no_entries_after on a US winter and a
    US summer FX day, rules 1 to 4 in order. Uses the part 03 time helpers."""
    day_end, day = cfg["day_end"], cfg["day"]
    flat, last = day["flat_by"], day["no_entries_after"]
    q = datetime.timedelta(minutes=15)
    for t, season in D15_DAYS:
        s, e = fx_day_bounds(t, day_end)
        r = e if day_end == "17:00 New York" else ny_to_utc(at_time(parse_fx_date(t), "17:00"))
        f = pkt_time_on_fx_day(flat, t, day_end)
        n = pkt_time_on_fx_day(last, t, day_end)
        or_end = opening_range_window(day["or_session"], t, day["or_minutes"])[1]
        if f < s + datetime.timedelta(minutes=60):
            raise ValueError('%s: "day.flat_by" %s falls in the first hour of the FX day in %s '
                             '(that hour starts at %s). Pick a time like 23:30.'
                             % (path, flat, season, _pkt_hhmm(s)))
        if f > r - q:
            raise ValueError('%s: "day.flat_by" %s is too close to the 17:00 New York rollover in '
                             '%s (%s). Pick 01:45 or earlier.' % (path, flat, season, _pkt_hhmm(r)))
        if n > f - q:
            raise ValueError('%s: "day.no_entries_after" %s must be at least 15 minutes before '
                             '"day.flat_by" %s' % (path, last, flat))
        if n <= or_end:
            raise ValueError('%s: "day.no_entries_after" %s is before the %s opening range ends '
                             '(%s in %s). No day trade could start.'
                             % (path, last, SESSION_WORDS[day["or_session"]], _pkt_hhmm(or_end), season))


def _check_news(cfg, path):
    """The news.* keys and calendar_url of 01 3.2."""
    _cfg_int(cfg, "news.quiet_before_min", 0, 240, path)
    _cfg_int(cfg, "news.quiet_after_min", 0, 240, path)
    imp = _cfg_get(cfg, "news.impacts")
    if not isinstance(imp, list) or not imp or not all(x in ("High", "Medium", "Low") for x in imp):
        raise _cfg_bad(path, "news.impacts", "some of High, Medium, Low", imp)
    feeds = _cfg_get(cfg, "news.feeds")
    bad = '%s: "news.feeds": each feed needs a "name" (letters, digits, -) and a "url"' % path
    if not isinstance(feeds, list):
        raise ValueError(bad)
    names = set()
    for feed in feeds:
        name = feed.get("name") if isinstance(feed, dict) else None
        url = feed.get("url") if isinstance(feed, dict) else None
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9-]{1,40}", name):
            raise ValueError(bad)
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            raise ValueError(bad)
        if name in names:
            raise ValueError('%s: "news.feeds" lists "%s" twice' % (path, name))
        names.add(name)
    url = cfg.get("calendar_url")
    if not isinstance(url, str) or (url and not url.startswith("https://")):
        raise ValueError('%s: "calendar_url" must start with https:// or be "" (off)' % path)


def _check_dates(cfg, path):
    """holidays, early_closes and fx_rates_max_age_hours of 01 3.2."""
    hol = cfg.get("holidays")
    if not isinstance(hol, dict) or not all(_is_date(k) and isinstance(v, str) for k, v in hol.items()):
        raise ValueError('%s: "holidays" wants {"2026-12-25": "Christmas"}' % path)
    early = cfg.get("early_closes")
    if not isinstance(early, dict) or not all(_is_date(k) and _is_hhmm(v) for k, v in early.items()):
        raise ValueError('%s: "early_closes" wants {"2026-11-27": "13:45"} (New York time)' % path)
    for d in sorted(early):
        if early[d] >= "17:00":
            raise ValueError('%s: "early_closes" %s %s must be before 17:00 New York (the day '
                             'ends then)' % (path, d, early[d]))
    _cfg_number(cfg, "fx_rates_max_age_hours", 1, 168, path)


def _plain(v):
    """A value for a message: numbers without exponents (0.00001, not 1e-05); else JSON."""
    if _is_number(v):
        return format(Decimal(repr(v)), "f")
    return json.dumps(v)


def _row_bad(col, want, v):
    """The generic column message (the caller adds the path and the key)."""
    return ValueError('"%s" must be %s (you have %s)' % (col, want, _plain(v)))


def _row_number(row, col, lo, hi=None, null_ok=False, lo_open=False):
    """row[col], a number from lo to hi (above lo when lo_open; no top when hi is None)."""
    v = row.get(col)
    if v is None and null_ok:
        return None
    low_ok = _is_number(v) and (v > lo if lo_open else v >= lo)
    if not low_ok or (hi is not None and v > hi):
        want = ("a number above %s" if lo_open else "a number from %s") % _num_text(lo)
        if hi is not None:
            want += (", up to %s" if lo_open else " to %s") % _num_text(hi)
        raise _row_bad(col, want, v)
    return v


def _row_int(row, col, lo, hi):
    """row[col], a whole number from lo to hi. 3.0 is stored back as 3."""
    v = row.get(col)
    if not _is_number(v) or v != int(v) or not lo <= v <= hi:
        raise _row_bad(col, "a whole number from %s to %s" % (_num_text(lo), _num_text(hi)), v)
    row[col] = int(v)
    return int(v)


def _row_choice(row, col, choices):
    """row[col], which must be one of choices."""
    v = row.get(col)
    if not isinstance(v, str) or v not in choices:
        raise _row_bad(col, "one of " + ", ".join(choices), v)
    return v


def _inst_size(row):
    """kind, price_source, digits and point, tick_size, contract_size and the lots (01 4.6)."""
    _row_choice(row, "kind", KINDS)
    _row_choice(row, "price_source", PRICE_SOURCES)
    digits = _row_int(row, "digits", 0, 8)
    point = Decimal(1).scaleb(-digits)
    p = row.get("point")
    if p is not None and (not _is_number(p) or _dec_clean(p) != point):
        raise ValueError('"point" %s does not match "digits" %d (that gives %s)'
                         % (_plain(p), digits, "%.*f" % (digits, point)))
    tick = row.get("tick_size")
    if tick is not None:
        steps = _dec_clean(tick) / point if _is_number(tick) else Decimal("0.5")
        if steps != int(steps) or not 1 <= steps <= 1000:
            raise ValueError('"tick_size" must be a whole number of points')
    if not _is_number(row.get("contract_size")) or row["contract_size"] <= 0:
        raise ValueError('"contract_size" must be above 0. Copy "Contract size" from MetaTrader.')
    lo = _row_number(row, "min_lot", 0, lo_open=True)
    hi = _row_number(row, "max_lot", 0, lo_open=True)
    step = _row_number(row, "lot_step", 0, lo_open=True)
    if lo > hi:
        raise ValueError('"min_lot" must not be above "max_lot"')
    if _dec_clean(lo) % _dec_clean(step) != 0:
        raise ValueError('"min_lot" %s is not a whole number of "lot_step" %s' % (_plain(lo), _plain(step)))


def _inst_money(row):
    """calc_mode, swaps, hours, spread, commission, fees, leverage, margin and the unit
    factor (01 4.6), in table order."""
    _row_choice(row, "calc_mode", CALC_MODES)
    if row.get("swap_mode") not in SWAP_MODES:
        raise ValueError('"swap_mode" must be one of ' + ", ".join(SWAP_MODES))
    if row.get("swap_triple_day") not in TRIPLE_DAYS:
        raise ValueError('"swap_triple_day" must be monday to friday, or none. MetaTrader charges '
                         'no swap on Saturday or Sunday nights.')
    _row_choice(row, "hours", BROKER_TEMPLATES)
    _row_choice(row, "data_hours", DATA_TEMPLATES)
    spread = row.get("spread_points")
    if _is_number(spread) and 0 < spread < 1:
        raise ValueError('"spread_points" is in points, not price. 0.09 for gold looks like a '
                         'price: use 9.')
    _row_number(row, "spread_points", 0, 100000)
    _row_number(row, "commission_per_lot_side", 0, 1000)
    for col in ("swap_long", "swap_short"):
        if not _is_number(row.get(col)):
            raise ValueError('"%s" must be a number, like -6.23' % col)
    _row_number(row, "swap_free_fee_per_lot", 0, 1000, null_ok=True)
    _row_int(row, "swap_free_grace_nights", 0, 60)
    _row_number(row, "leverage", 1, 5000, null_ok=True)
    _row_number(row, "initial_margin", 0, null_ok=True, lo_open=True)
    _row_number(row, "margin_rate", 0, 100, lo_open=True)
    _row_number(row, "yahoo_to_broker", 0, lo_open=True)
    _row_int(row, "stops_level_points", 0, 100000)   # (check) not in the 4.6 table


def _inst_futures(row):
    """The futures object of a futures row (01 4.5 and 4.6)."""
    fut = row.get("futures")
    if not isinstance(fut, dict) or not all(k in fut for k in FUTURES_KEYS):
        raise ValueError('"futures" needs ' + ", ".join(FUTURES_KEYS))
    if not isinstance(fut["root"], str) or not fut["root"]:
        raise _row_bad("futures.root", 'a Yahoo root like "GC"', fut["root"])
    if fut["suffix"] not in FUTURES_SUFFIXES:
        raise _row_bad("futures.suffix", "one of " + ", ".join(FUTURES_SUFFIXES), fut["suffix"])
    cycle = fut["cycle"]
    if not isinstance(cycle, str) or not cycle or not all(c in FUTURES_MONTH_LETTERS for c in cycle):
        raise _row_bad("futures.cycle", "month letters from " + FUTURES_MONTH_LETTERS, cycle)
    if fut["roll_rule"] not in ROLL_RULES:
        raise _row_bad("futures.roll_rule", "one of " + ", ".join(ROLL_RULES), fut["roll_rule"])
    settle = fut["settle"]
    if not (isinstance(settle, list) and len(settle) == 2 and settle[0] in ("ny", "london")
            and _is_hhmm(settle[1])):
        raise _row_bad("futures.settle", 'like ["ny", "13:30"]', settle)
    for col in ("shortname_month", "history_check"):
        if not isinstance(fut[col], bool):
            raise _row_bad("futures." + col, "true or false", fut[col])


def _check_instrument(key, row, path):
    """One instrument row: the needed columns of a new key (01 4.1), then 01 4.6 in order.
    The message is <path>: instrument "<KEY>": <what>."""
    try:
        if not isinstance(row, dict):
            raise ValueError("must be a JSON object: {...}")
        if key not in BUILTIN_INSTRUMENTS:
            need = NEEDED_COLUMNS + (("futures",) if row.get("price_source") == "futures" else ())
            for col in need:
                if row.get(col) is None:
                    raise ValueError('a new instrument needs "%s". Copy it from MetaTrader '
                                     '(the built-in rows show each column).' % col)
        _inst_size(row)
        _inst_money(row)
        if row["price_source"] == "futures":
            _inst_futures(row)
        if row.get("profit_currency") not in PROFIT_CURRENCIES:
            raise ValueError('"profit_currency" %s has no Yahoo rate in this pack'
                             % json.dumps(row.get("profit_currency")))
        mc = row.get("margin_currency")
        if mc not in PROFIT_CURRENCIES and mc != row.get("base"):
            raise ValueError('"margin_currency" %s must be USD, a currency of this pack or the '
                             'base %s' % (json.dumps(mc), row.get("base")))
    except ValueError as exc:
        raise ValueError('%s: instrument "%s": %s' % (path, key, exc))


def check_config(cfg, path="fx/config.json"):
    """Check a whole config (01 3.2, 3.3, 4.1 and 4.6), in table order. The first bad
    value raises ValueError "<path>: ..."; whole-number values are stored back as ints."""
    if not isinstance(cfg, dict):
        raise ValueError("%s must be a JSON object: {...}" % path)
    for key in MERGED_KEYS + ("instruments",):
        if not isinstance(cfg.get(key), dict):
            raise ValueError('%s: "%s" must be a JSON object: {...}' % (path, key))
    _check_account(cfg, path)
    _check_top(cfg, path)
    _check_risk(cfg, path)
    _check_swing(cfg, path)
    _check_day(cfg, path)
    _check_news(cfg, path)
    _check_dates(cfg, path)
    for key, row in cfg["instruments"].items():
        _check_instrument(key, row, path)
    return cfg


def _find_secret(doc):
    """The first key named like a login or a password (any case) anywhere in doc; else None."""
    if isinstance(doc, dict):
        for k, v in doc.items():
            if isinstance(k, str) and k.lower() in SECRET_KEYS:
                return k
            hit = _find_secret(v)
            if hit is not None:
                return hit
    elif isinstance(doc, list):
        for v in doc:
            hit = _find_secret(v)
            if hit is not None:
                return hit
    return None


def _fill_derived(key, row):
    """Fill broker_symbol (the key), point (10 ** -digits) and tick_size (the point)
    when the row does not give them (01 3.1 step 5)."""
    if isinstance(row, dict) and not row.get("broker_symbol"):
        row["broker_symbol"] = key
    digits = row.get("digits") if isinstance(row, dict) else None
    if not _is_number(digits) or digits != int(digits) or not 0 <= digits <= 8:
        return
    point = round(10 ** -int(digits), int(digits))
    if row.get("point") is None:
        row["point"] = point
    if row.get("tick_size") is None:
        row["tick_size"] = point


def merge_config(doc, path="fx/config.json"):
    """A deep copy of DEFAULTS with the user's file doc on top (01 3.1 steps 1 to 5):
    account, risk, swing, day and news key by key; instruments column by column; the
    rest replaced whole. A new instrument key starts from the column defaults of 01 4.1."""
    cfg = copy.deepcopy(DEFAULTS)
    for key, value in doc.items():
        value = copy.deepcopy(value)
        if key in MERGED_KEYS or key == "instruments":
            if not isinstance(value, dict):
                raise ValueError('%s: "%s" must be a JSON object: {...}' % (path, key))
        if key in MERGED_KEYS:
            cfg[key].update(value)
        elif key == "instruments":
            for sym, row in value.items():
                if not isinstance(row, dict):
                    raise ValueError('%s: instrument "%s": must be a JSON object: {...}' % (path, sym))
                if sym not in cfg["instruments"]:
                    cfg["instruments"][sym] = dict(COLUMN_DEFAULTS)
                cfg["instruments"][sym].update(row)
        else:
            cfg[key] = value
    for sym, row in cfg["instruments"].items():
        _fill_derived(sym, row)
    return cfg


def config_warnings(doc, cfg, path="fx/config.json"):
    """The warning lines of a loaded config (01 3.1 step 7 and 3.2): unknown top-level
    keys of the file, and a UTC+0 server with the default day_end."""
    lines = ['%s: unknown key "%s" (skipped). Check the spelling.' % (path, k)
             for k in doc if k not in DEFAULTS]
    if server_offset(cfg["account"]["server_time"]) == 0 and cfg["day_end"] == DEFAULTS["day_end"]:
        lines.append('Your server clock is UTC+0. Such brokers often end the day at 00:00 UTC. '
                     'Check "day_end".')
    return lines


def load_config(path, required=False):
    """Read the config: DEFAULTS, the file on top, then check_config; warnings go to
    stderr. A missing file gives the DEFAULTS and CONFIG_NOTE, but when required (the
    user typed --config FILE) it is an error (01 1.2). Logins are refused."""
    doc = load_json(path)
    if doc is None and required:
        raise ValueError("no config file at %s (copy config.example.json from the "
                         "fx-budget folder)" % path)
    if doc is None:
        sys.stderr.write(CONFIG_NOTE + "\n")
        return check_config(merge_config({}, path), path)
    if not isinstance(doc, dict):
        raise ValueError("%s must be a JSON object: {...}" % path)
    secret = _find_secret(doc)
    if secret is not None:
        raise ValueError('%s: remove "%s". Never put MetaTrader logins or passwords in the '
                         "pack's files." % (path, secret))
    cfg = check_config(merge_config(doc, path), path)
    for line in config_warnings(doc, cfg, path):
        sys.stderr.write(line + "\n")
    return cfg


# ---------------------------------------------------------------- shared by skill scripts
# group: shared by skill scripts

# Helpers that two or more skill scripts use (decision 2 of the build brief).

RECENT_FX_DAYS = 5        # roll notes and outage lines look this many FX days back
WRAP = 78              # the line width of fx-scanner and fx-entry
WIDTH = 78             # the line width of fx-news and fx-risk


def back_fx_day(fx_date, n, cfg):
    """The FX day n FX days before fx_date (weekends and full FX holidays skipped)."""
    d = parse_fx_date(fx_date, weekend_ok=True)
    for _ in range(n):
        d -= datetime.timedelta(days=1)
        while d.weekday() >= 5 or full_fx_holiday(d, cfg):
            d -= datetime.timedelta(days=1)
    return d.isoformat()


def recent_start(fx_date, cfg):
    """The start (UTC) of the window "the last 5 FX days" that ends with fx_date."""
    day_end = cfg.get("day_end", "17:00 New York")
    return fx_day_bounds(back_fx_day(fx_date, RECENT_FX_DAYS - 1, cfg), day_end)[0]


def contract_month(symbol, root):
    """"GCQ26.CMX" -> "Aug" (by MONTH_CODES), or None."""
    letter = str(symbol or "")[len(root):len(root) + 1]
    for month, code in MONTH_CODES.items():
        if code == letter:
            return month
    return None


def roll_window_date(w):
    """The first FX day of an expected roll window (its start is 17:00 New York the day
    before), as "YYYY-MM-DD"."""
    n = to_ny(w[2])
    d = n.date() + datetime.timedelta(days=1 if n.hour >= 17 else 0)
    return d.isoformat()


def fx_day_of(utc, day_end):
    """The FX day of a moment; a weekend moment gives its New York date."""
    return fx_day(utc, day_end) or to_ny(utc).date().isoformat()


def rolls_known(rolls, now):
    """The rolls already found at now: a replay sees a roll only from its found_at on."""
    out = []
    for r in rolls:
        found = parse_utc(r.get("found_at"))
        if found is None or found <= now:
            out.append(r)
    return out


def roll_notes(inst, rolls, now, fx_day, cfg, data_dir):
    """The roll lines of a futures hit (section 4.5): a roll ahead in the next 5 FX days,
    a roll in the last 5 FX days, a roll that the history search missed."""
    root = inst_root(inst)
    if not root or not inst.get("futures"):
        return []
    day_end = cfg.get("day_end", "17:00 New York")
    name = inst.get("name") or inst["key"]
    word = ROLL_WORDS.get(root, name.lower())
    mine = [r for r in rolls if r.get("root") == root]

    def roll_in(a, b):
        return any(a <= (parse_utc(r.get("first_bar_new")) or a - datetime.timedelta(1)) <= b
                   for r in mine)

    out = []
    horizon = fx_day_bounds(add_fx_days(fx_day, RECENT_FX_DAYS, cfg), day_end)[1]
    for w in expected_rolls(root, inst, now, horizon):
        if w[2] < horizon and w[3] > now and not roll_in(w[2], w[3]):
            old_m, new_m = contract_month(w[0], root), contract_month(w[1], root)
            if w[2] > now:
                when = roll_window_date(w)
            else:   # the window is open now (check: the spec shows only a date)
                when = "now, by %s at the latest" % to_ny(w[3]).date().isoformat()
            out.append("%s rolls about %s (%s to %s): Yahoo prices jump then."
                       % (name, when, old_m, new_m))
            break
    since = recent_start(fx_day, cfg)
    for r in mine:
        first = parse_utc(r.get("first_bar_new"))
        if first is not None and since <= first <= now and r.get("ratio"):
            pct = (float(r["ratio"]) - 1.0) * 100.0
            out.append("%s rolled on Yahoo on %s (%+.2f%%). Older prices are adjusted to match."
                       % (name, day_label(parse_fx_date(fx_day_of(first, day_end), weekend_ok=True)), pct))
    if (inst["futures"] or {}).get("history_check"):
        closes = _daily_closes(data_dir, inst, now, cfg, True)
        today = to_ny(now).date()
        for w in expected_rolls(root, inst, since, now):
            if w[3] <= now and w[3] >= since and w[4] <= today and w[4] in closes \
                    and not roll_in(w[2], w[3]):
                out.append("Could not find the %s roll of about %s in Yahoo data. ATR and "
                           "setups near that date may be off." % (word, w[4].isoformat()))
    return out


def pct_text(x):
    """A percent for people: "1.0%", "0.5%", "0.25%": at least 1 decimal, at most 2."""
    d = Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP).normalize()
    text = format(d, "f")
    if "." not in text:
        text += ".0"
    return text + "%"


def fresh_quote(data_dir, paths, inst, now, cfg, replay):
    """latest_price, or a newer prices.json quote of the same Yahoo symbol (not after
    now, never in a replay): {bid, at, age_min, source}. None when there is no price."""
    lp = latest_price(data_dir, inst, now, cfg)
    if replay:
        return lp
    try:
        doc = load_json(paths["prices"]) or {}
        q = (doc.get("quotes") or {}).get(inst["key"]) if isinstance(doc, dict) else None
        if not isinstance(q, dict) or q.get("yahoo") != inst["yahoo"]:
            return lp
        at = parse_utc(q.get("at"))
        bid = q.get("bid")
        if at is None or at > now or isinstance(bid, bool) or not isinstance(bid, (int, float)) or bid <= 0:
            return lp
        if lp is not None and lp.get("at") is not None and at <= lp["at"]:
            return lp
        return {"bid": bid, "yahoo": bid, "at": at, "age_min": max(0.0, (now - at).total_seconds() / 60.0),
                "source": "prices.json", "contract": (lp or {}).get("contract")}
    except ValueError:
        return lp
