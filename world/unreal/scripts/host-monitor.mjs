import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { open, readFile, writeFile } from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';

export async function startHostMonitor(output, duration) {
  assert(Number.isInteger(duration) && duration >= 60 && duration <= 1800);
  const report = {
    startedAt: new Date().toISOString(),
    bootId: (await readFile('/proc/sys/kernel/random/boot_id', 'utf8')).trim(),
    kernel: (await readFile('/proc/sys/kernel/osrelease', 'utf8')).trim(),
    memoryBefore: await readFile('/proc/meminfo', 'utf8'),
    intervalSeconds: 5,
    log: `${output}/host-vmstat.log`,
    firstRow: 'CPU and I/O averages since boot; subsequent rows cover five seconds',
  };
  await writeFile(`${output}/host.json`, JSON.stringify(report, null, 2) + '\n', { flag: 'wx' });
  const stdout = await open(report.log, 'wx');
  const stderr = await open(`${output}/host-vmstat-stderr.log`, 'wx');
  const child = spawn('/usr/bin/vmstat', ['-w', '-t', '5', String(Math.ceil(duration / 5) + 120)], {
    stdio: ['ignore', stdout.fd, stderr.fd],
  });
  const closed = new Promise((accept) => {
    child.once('error', (error) => {
      report.failure = error.message;
    });
    child.once('close', (code, signal) => accept({ code, signal }));
  });
  await stdout.close();
  await stderr.close();
  return async function stopHostMonitor() {
    const requestedStop = child.kill('SIGTERM');
    report.exit = await Promise.race([
      closed,
      delay(5000, undefined, { ref: false }).then(() => {
        throw new Error('Host monitor did not stop within five seconds');
      }),
    ]);
    report.endedAt = new Date().toISOString();
    report.memoryAfter = await readFile('/proc/meminfo', 'utf8');
    await writeFile(`${output}/host.json`, JSON.stringify(report, null, 2) + '\n');
    assert(requestedStop && report.exit.signal === 'SIGTERM', JSON.stringify(report));
    assert.equal(await readFile(`${output}/host-vmstat-stderr.log`, 'utf8'), '');
    return report;
  };
}
