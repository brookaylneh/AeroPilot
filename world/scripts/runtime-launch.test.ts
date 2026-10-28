import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

const project = path.resolve(import.meta.dirname, '../..');

test('packaged container needs no editor, accepts bounded host resources and selects the runtime image', () => {
  const directory = mkdtempSync(path.join(tmpdir(), 'snr-launch-'));
  try {
    const output = path.join(directory, 'docker-args.json');
    writeFileSync(
      path.join(directory, 'docker'),
      `#!${process.execPath}\nrequire('node:fs').writeFileSync(process.env.SNR_TEST_ARGUMENTS, JSON.stringify(process.argv.slice(2)));\n`,
      { mode: 0o700 },
    );
    const env = {
      ...process.env,
      PATH: `${directory}:${process.env.PATH}`,
      SNR_TEST_ARGUMENTS: output,
      UE_ROOT: path.join(directory, 'editor-not-installed'),
      SNR_CPU_LIMIT: '4',
      SNR_MEMORY_LIMIT: '24g',
      SNR_UNREAL_IMAGE: 'snr-unreal-runtime:test',
      SNR_STREAM_CODEC: 'H264',
    };
    const run = spawnSync('bash', ['world/scripts/unreal-container.sh', 'run-packaged'], {
      cwd: project,
      env,
      encoding: 'utf8',
    });
    assert.equal(run.status, 0, run.stderr);
    const args: string[] = JSON.parse(readFileSync(output, 'utf8'));
    assert(args.includes('--memory=24g'));
    assert(args.includes('--cpus=4'));
    assert(args.includes('--runtime=nvidia'));
    assert(args.includes('snr-unreal-runtime:test'));
    assert(!args.some((value) => value.includes('editor-not-installed')));
    const badMemory = spawnSync('bash', ['world/scripts/unreal-container.sh', 'run-packaged'], {
      cwd: project,
      env: { ...env, SNR_MEMORY_LIMIT: 'unlimited' },
      encoding: 'utf8',
    });
    assert.equal(badMemory.status, 2);
    const build = spawnSync('bash', ['world/scripts/unreal-container.sh', 'build'], {
      cwd: project,
      env,
      encoding: 'utf8',
    });
    assert.equal(build.status, 2);
    assert.match(build.stderr, /Missing Unreal SDK/);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test('runtime selects its manifest and H264 explicitly; missing maps and unsupported codecs fail', () => {
  const directory = mkdtempSync(path.join(tmpdir(), 'snr-package-'));
  try {
    const binaries = path.join(directory, 'ScenarioStudio/Binaries/Linux');
    mkdirSync(binaries, { recursive: true });
    const output = path.join(directory, 'engine-args.json');
    writeFileSync(
      path.join(binaries, 'ScenarioStudio'),
      `#!${process.execPath}\nrequire('node:fs').writeFileSync(process.env.SNR_TEST_ARGUMENTS, JSON.stringify(process.argv.slice(2)));\n`,
      { mode: 0o700 },
    );
    writeFileSync(path.join(directory, 'ScenarioStudio.sh'), '#!/bin/sh\nexit 0\n', { mode: 0o700 });
    writeFileSync(path.join(directory, 'scenario-map.txt'), '/Game/OpenMeadow/OpenMeadow\n');
    writeFileSync(path.join(directory, 'settings.json'), '{}');
    const env: NodeJS.ProcessEnv = {
      ...process.env,
      HOME: directory,
      SNR_TEST_ARGUMENTS: output,
      SNR_PACKAGE_ROOT: directory,
      SNR_COSYS_SETTINGS: path.join(directory, 'settings.json'),
      SNR_SCENARIO_DATA_DIR: path.join(directory, 'scenarios'),
      SNR_DRONE_DATA_DIR: '',
      SNR_STREAM_CODEC: 'H264',
      SNR_STREAM_FPS: '60',
      SNR_STREAM_HEIGHT: '1080',
      SNR_STREAM_MAX_BITRATE: '40000000',
    };
    const run = () =>
      spawnSync('bash', ['world/scripts/run-packaged.sh'], { cwd: project, env, encoding: 'utf8' });
    assert.equal(run().status, 0);
    const args: string[] = JSON.parse(readFileSync(output, 'utf8'));
    assert(args.includes('/Game/OpenMeadow/OpenMeadow'));
    assert(args.includes('-PixelStreamingEncoderCodec=H264'));
    assert(args.includes('-ResX=1920'));
    assert(args.includes('-PixelStreamingWebRTCFps=60'));
    assert(args.includes('-PixelStreamingWebRTCMaxBitrate=40000000'));
    assert(args.includes('-PixelStreamingWebRTCStartBitrate=40000000'));
    assert(args.includes('-PixelStreamingWebRTCMinBitrate=1000000'));
    assert(args.includes('-PixelStreamingEncoderQualityPreset=HighQuality'));
    assert(args.includes('-PixelStreamingEncoderLatencyMode=LowLatency'));
    assert(!args.some((arg) => arg.startsWith('-PixelStreamingEncoderKeyframeInterval')));
    assert(!args.some((arg) => arg.startsWith('-PixelStreamingEncoderTargetBitrate')));
    assert(args.some((arg) => arg.startsWith('-dpcvars=') && arg.includes('r.ScreenPercentage=100')));
    env.SNR_STREAM_MAX_BITRATE = 'unlimited';
    assert.equal(run().status, 2, 'Unsupported bandwidth must fail explicitly');
    env.SNR_STREAM_MAX_BITRATE = '25000000';
    env.SNR_STREAM_MIN_BITRATE = '10000000';
    assert.equal(run().status, 0);
    const floored: string[] = JSON.parse(readFileSync(output, 'utf8'));
    assert(floored.includes('-PixelStreamingWebRTCMinBitrate=10000000'));
    assert(floored.includes('-PixelStreamingWebRTCStartBitrate=25000000'));
    env.SNR_STREAM_MIN_BITRATE = '2000000';
    assert.equal(run().status, 2, 'Unsupported floor must fail explicitly');
    delete env.SNR_STREAM_MIN_BITRATE;
    env.SNR_STREAM_TARGET_BITRATE = '15000000';
    assert.equal(run().status, 0);
    assert(JSON.parse(readFileSync(output, 'utf8')).includes('-PixelStreamingEncoderTargetBitrate=15000000'));
    delete env.SNR_STREAM_TARGET_BITRATE;
    env.SNR_STREAM_MAX_BITRATE = '40000000';
    env.SNR_STREAM_CODEC = 'not-a-codec';
    assert.equal(run().status, 2);
    env.SNR_STREAM_CODEC = 'H264';
    rmSync(path.join(directory, 'scenario-map.txt'));
    assert.notEqual(run().status, 0, 'A missing map must never load a substitute world');
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
