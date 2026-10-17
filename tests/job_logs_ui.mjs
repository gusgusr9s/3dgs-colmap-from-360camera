import assert from 'node:assert/strict';
import {formatLogTime} from '../job_logs.mjs';

assert.equal(formatLogTime('[2026-10-17T16:30:58.228+00:00] progress'),
  '[2026-10-17 23:30:58.228 UTC+7] progress');
assert.equal(formatLogTime('[2026-10-17T23:30:58.228+07:00] progress'),
  '[2026-10-17 23:30:58.228 UTC+7] progress');
assert.equal(formatLogTime('2026-10-17T20:00:00Z'), '2026-10-18 03:00:00.000 UTC+7');
assert.equal(formatLogTime('2026-10-17T08:00:00-04:00'), '2026-10-17 19:00:00.000 UTC+7');
const native='I20261017 23:25:03.970435 30368 Global bundle adjustment; iter 17000';
assert.equal(formatLogTime(native),native);
const raw='{"at":"2026-10-17T16:31:11.579+00:00","path":"/jobs/x/3d.log"}';
assert(formatLogTime(raw).includes('2026-10-17 23:31:11.579 UTC+7'));
assert(raw.includes('T16:31:11.579+00:00'));
console.log('Log timestamps display Bangkok time, preserve native clocks and leave raw input unchanged.');
