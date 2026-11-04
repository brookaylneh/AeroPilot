import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

const project = path.resolve(import.meta.dirname, '../../..');

test('packaging rejects missing manifest inputs before creating a build', () => {
  const directory = mkdtempSync(path.join(tmpdir(), 'snr-package-inputs-'));
  try {
    const manifest = path.join(directory, 'manifest.json');
    const archive = path.join(directory, 'archive');
    writeFileSync(manifest, '{}');
    const env: NodeJS.ProcessEnv = {
      ...process.env,
      UE_ROOT: path.join(directory, 'engine'),
      SNR_WORLD_CONFIG: manifest,
      SNR_COSYS_SETTINGS: manifest,
    };
    for (const key of ['SNR_WORLD_CONFIG', 'SNR_COSYS_SETTINGS']) {
      for (const value of [undefined, path.join(directory, 'missing.json')]) {
        const run = spawnSync(
          'bash',
          ['world/unreal/scripts/package-unreal.sh', archive, path.join(directory, 'evidence')],
          { cwd: project, env: { ...env, [key]: value }, encoding: 'utf8' },
        );
        assert.notEqual(run.status, 0, `${key} must identify an existing manifest`);
        assert(!existsSync(archive));
      }
    }
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

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
      SNR_UNREAL_HOME: path.join(directory, 'home'),
      SNR_PACKAGE_ROOT: directory,
      SNR_PIXEL_STREAMING: '1',
      SNR_STREAM_CODEC: 'H264',
      SNR_PROFILE: '0',
      SNR_RENDER_PERCENTAGE: '100',
      SNR_STREAMER_PORT: '8888',
      SNR_STREAMER_ID: 'DefaultStreamer',
      SNR_WEBRTC_MIN_PORT: '49160',
      SNR_WEBRTC_MAX_PORT: '49200',
      SNR_STREAM_FPS: '60',
      SNR_STREAM_MAX_BITRATE: '40000000',
      SNR_STREAM_MIN_BITRATE: '1000000',
      SNR_STREAM_HEIGHT: '1080',
      SNR_MEDIA_CAPTURE: '1',
      SNR_RHI_THREAD: '1',
      SNR_COSYS_SETTINGS: path.join(directory, 'settings.json'),
      SNR_SCENARIO_DATA_DIR: path.join(directory, 'scenarios'),
    };
    const run = spawnSync('bash', ['world/unreal/scripts/unreal-container.sh', 'run-packaged'], {
      cwd: project,
      env,
      encoding: 'utf8',
    });
    assert.equal(run.status, 0, run.stderr);
    const args: string[] = JSON.parse(readFileSync(output, 'utf8'));
    assert(args.includes('--memory=24g'));
    assert(args.includes('--cpus=4'));
    assert(args.includes('--runtime=nvidia'));
    for (const device of ['nvidia0', 'nvidiactl', 'nvidia-uvm', 'nvidia-uvm-tools', 'nvidia-modeset'])
      assert(args.includes(`--device=/dev/${device}`));
    assert(args.includes('snr-unreal-runtime:test'));
    assert(args.includes(`type=bind,src=${directory},dst=${directory},readonly`));
    assert(!args.some((value) => value.includes('editor-not-installed')));
    const badMemory = spawnSync('bash', ['world/unreal/scripts/unreal-container.sh', 'run-packaged'], {
      cwd: project,
      env: { ...env, SNR_MEMORY_LIMIT: 'unlimited' },
      encoding: 'utf8',
    });
    assert.equal(badMemory.status, 2);
    const build = spawnSync('bash', ['world/unreal/scripts/unreal-container.sh', 'build'], {
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

test('runtime selects its map, shader platform and H264 explicitly; invalid manifests fail', () => {
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
    writeFileSync(path.join(directory, 'shader-platform.txt'), 'SF_VULKAN_SM6\n');
    writeFileSync(path.join(directory, 'settings.json'), '{}');
    const env: NodeJS.ProcessEnv = {
      ...process.env,
      HOME: directory,
      SNR_TEST_ARGUMENTS: output,
      SNR_PACKAGE_ROOT: directory,
      SNR_PIXEL_STREAMING: '1',
      SNR_COSYS_SETTINGS: path.join(directory, 'settings.json'),
      SNR_SCENARIO_DATA_DIR: path.join(directory, 'scenarios'),
      SNR_DRONE_DATA_DIR: '',
      SNR_STREAM_CODEC: 'H264',
      SNR_PROFILE: '0',
      SNR_RENDER_PERCENTAGE: '100',
      SNR_STREAMER_PORT: '8888',
      SNR_STREAMER_ID: 'DefaultStreamer',
      SNR_WEBRTC_MIN_PORT: '49160',
      SNR_WEBRTC_MAX_PORT: '49200',
      SNR_STREAM_FPS: '60',
      SNR_STREAM_MIN_BITRATE: '1000000',
      SNR_STREAM_HEIGHT: '1080',
      SNR_STREAM_MAX_BITRATE: '40000000',
      SNR_MEDIA_CAPTURE: '1',
      SNR_RHI_THREAD: '1',
    };
    const run = () =>
      spawnSync('bash', ['world/unreal/scripts/run-packaged.sh'], { cwd: project, env, encoding: 'utf8' });
    assert.equal(run().status, 0);
    const args: string[] = JSON.parse(readFileSync(output, 'utf8'));
    assert(args.includes('/Game/OpenMeadow/OpenMeadow'));
    assert(args.includes('-sm6'));
    assert(!args.includes('-sm5'));
    assert(args.includes('-PixelStreamingEncoderCodec=H264'));
    assert(args.includes('-ResX=1920'));
    assert(!args.includes('-logPSO'));
    env.SNR_PROFILE = '1';
    assert.equal(run().status, 0);
    assert(JSON.parse(readFileSync(output, 'utf8')).includes('-logPSO'));
    env.SNR_PROFILE = 'invalid';
    assert.equal(run().status, 2);
    env.SNR_PROFILE = '0';
    assert(args.includes('-PixelStreamingWebRTCFps=60'));
    assert(args.includes('-PixelStreamingWebRTCMaxBitrate=40000000'));
    assert(args.includes('-PixelStreamingWebRTCStartBitrate=40000000'));
    assert(args.includes('-PixelStreamingWebRTCMinBitrate=1000000'));
    assert(args.includes('-PixelStreamingEncoderQualityPreset=HighQuality'));
    assert(args.includes('-PixelStreamingEncoderLatencyMode=LowLatency'));
    assert(!args.some((arg) => arg.startsWith('-PixelStreamingEncoderKeyframeInterval')));
    assert(!args.some((arg) => arg.startsWith('-PixelStreamingEncoderTargetBitrate')));
    assert(args.some((arg) => arg.startsWith('-dpcvars=') && arg.includes('r.ScreenPercentage=100')));
    assert(
      args.some((arg) => arg.startsWith('-dpcvars=') && arg.includes('PixelStreaming2.ID=DefaultStreamer')),
    );
    env.SNR_RENDER_PERCENTAGE = '75';
    assert.equal(run().status, 0);
    const temporalUpscaling: string[] = JSON.parse(readFileSync(output, 'utf8'));
    assert(temporalUpscaling.some((arg) => arg.includes('r.ScreenPercentage=75')));
    env.SNR_RENDER_PERCENTAGE = '0';
    assert.equal(run().status, 2);
    env.SNR_RENDER_PERCENTAGE = '100';
    env.SNR_STREAM_MAX_BITRATE = '80000000';
    assert.equal(run().status, 0);
    const highBitrate: string[] = JSON.parse(readFileSync(output, 'utf8'));
    assert(highBitrate.includes('-PixelStreamingWebRTCStartBitrate=80000000'));
    assert(highBitrate.includes('-PixelStreamingWebRTCMaxBitrate=80000000'));
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
    env.SNR_STREAM_MIN_BITRATE = '1000000';
    env.SNR_STREAM_TARGET_BITRATE = '15000000';
    assert.equal(run().status, 0);
    assert(JSON.parse(readFileSync(output, 'utf8')).includes('-PixelStreamingEncoderTargetBitrate=15000000'));
    delete env.SNR_STREAM_TARGET_BITRATE;
    env.SNR_STREAM_MAX_BITRATE = '40000000';
    env.SNR_STREAM_CODEC = 'not-a-codec';
    assert.equal(run().status, 2);
    env.SNR_STREAM_CODEC = 'H264';
    env.SNR_PIXEL_STREAMING = '0';
    env.SNR_STREAM_HEIGHT = '180';
    env.SNR_STREAM_FPS = '10';
    delete env.SNR_STREAMER_PORT;
    delete env.SNR_WEBRTC_MIN_PORT;
    delete env.SNR_WEBRTC_MAX_PORT;
    assert.equal(run().status, 0, 'A sensor-only instance needs no streaming ports');
    const sensorOnly: string[] = JSON.parse(readFileSync(output, 'utf8'));
    assert(sensorOnly.includes('-PixelStreamingInitializeDefaultStreamer=false'));
    assert(sensorOnly.includes('-PixelStreamingAutoStartStream=false'));
    assert(sensorOnly.includes('-ResX=320'));
    assert(sensorOnly.includes('-ResY=180'));
    assert(sensorOnly.includes('-ExecCmds=t.MaxFPS 10'));
    assert(!sensorOnly.some((arg) => arg.startsWith('-PixelStreamingConnectionURL=')));
    assert(!sensorOnly.some((arg) => arg.startsWith('-PixelStreamingWebRTCMinPort=')));
    writeFileSync(path.join(directory, 'shader-platform.txt'), 'SF_VULKAN_SM5\n');
    assert.equal(run().status, 0);
    const sm5: string[] = JSON.parse(readFileSync(output, 'utf8'));
    assert(sm5.includes('-sm5'));
    assert(!sm5.includes('-sm6'));
    writeFileSync(path.join(directory, 'shader-platform.txt'), 'unsupported\n');
    assert.equal(run().status, 2, 'An unsupported shader platform must fail');
    rmSync(path.join(directory, 'shader-platform.txt'));
    assert.notEqual(run().status, 0, 'A missing shader platform must fail');
    writeFileSync(path.join(directory, 'shader-platform.txt'), 'SF_VULKAN_SM6\n');
    rmSync(path.join(directory, 'scenario-map.txt'));
    assert.notEqual(run().status, 0, 'A missing map must never load a substitute world');
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test('editor MCP plugins are enabled only for the Editor target, never a packaged game', () => {
  const uproject = JSON.parse(
    readFileSync(path.join(project, 'world/unreal/ScenarioStudio/ScenarioStudio.uproject'), 'utf8'),
  );
  const editorOnly = uproject.Plugins.filter(
    (plugin: { TargetAllowList?: string[] }) => plugin.TargetAllowList,
  );
  assert.deepEqual(editorOnly, [
    { Name: 'ModelContextProtocol', Enabled: true, TargetAllowList: ['Editor'] },
    { Name: 'EditorToolset', Enabled: true, TargetAllowList: ['Editor'] },
  ]);
});
