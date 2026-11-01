import type { MissionEvent } from './missionState';

export function formatValue(value: number | null | undefined, unit: string, digits = 1) {
  const number =
    value === null || value === undefined
      ? '—'
      : (Number(value.toFixed(digits)) === 0 ? 0 : value).toFixed(digits);
  return number + (unit === '°' || unit === '%' ? unit : ` ${unit}`);
}

export function formatPosition(north_m: number, east_m: number) {
  const direction = (value: number, positive: string, negative: string) =>
    `${formatValue(Math.abs(value), 'm')} ${Number(value.toFixed(1)) < 0 ? negative : positive}`;
  return `${direction(north_m, 'north', 'south')} · ${direction(east_m, 'east', 'west')}`;
}

const pacific = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/Los_Angeles',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  fractionalSecondDigits: 3,
  hourCycle: 'h23',
  timeZoneName: 'short',
});
function pacificParts(ms: number) {
  return Object.fromEntries(pacific.formatToParts(ms).map((part) => [part.type, part.value]));
}
export function pacificClock(ms: number) {
  const p = pacificParts(ms);
  return `${p.hour}:${p.minute}:${p.second} ${p.timeZoneName}`;
}
export function pacificTimestamp(ms: number) {
  const p = pacificParts(ms);
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}:${p.second}.${p.fractionalSecond} ${p.timeZoneName}`;
}

const titles: Record<string, string> = {
  preview_plan: 'Preview mission plan',
  request_launch: 'Launch mission plan',
  apply_plan_change: 'Change mission plan',
  ground_height: 'Ground height',
  ground_profile: 'Ground profile',
  start_inspection: 'Inspect forward camera',
  set_camera_view: 'Centre and zoom camera',
  get_candidate: 'Review current candidate',
  center_candidate: 'Centre candidate camera',
  follow_candidate: 'Follow selected candidate',
  publish_evidence: 'Present evidence',
  disregard_and_continue: 'Dismiss candidate and continue',
  face_heading: 'Face heading',
  stop_inspection: 'Stop inspection',
  wait_for: 'Wait for mission event',
  stop: 'Stop',
  get_mission: 'Mission briefing',
  get_fleet: 'Fleet status',
  get_drone: 'Aircraft status',
  show_on_map: 'Focus map',
  open_feed: 'Live camera',
  take_off: 'Take off',
  fly_route: 'Fly route',
  fly_to: 'Fly to',
  hold: 'Hold',
  resume: 'Resume',
  return_home: 'Return home',
  land: 'Land',
  find_place: 'Find place',
  describe_location: 'Landmark directions',
};
function waypointCount(count: number) {
  return `${count} ${count === 1 ? 'waypoint' : 'waypoints'}`;
}
export function actionTitle(name: string, args: Record<string, unknown>) {
  const parts = [titles[name]];
  if (typeof args.drone_id === 'string') parts.push(args.drone_id);
  if (name === 'take_off' && typeof args.altitude_above_home_m === 'number')
    parts.push(formatValue(args.altitude_above_home_m, 'm', 0));
  if (name === 'fly_route' && Array.isArray(args.waypoints)) parts.push(waypointCount(args.waypoints.length));
  return parts.join(' · ');
}

export function approvalSentence(name: string, args: Record<string, unknown>) {
  if (name === 'request_launch') return `Launch mission plan version ${args.version}`;
  if (name === 'apply_plan_change') return `Launch additional aircraft for plan version ${args.version}`;
  if (name === 'take_off' && typeof args.altitude_above_home_m === 'number')
    return `Take off ${args.drone_id} to ${formatValue(args.altitude_above_home_m, 'm', 0)} above home`;
  throw new Error(`Unknown flight approval: ${name}`);
}

export function argumentRows(args: Record<string, unknown>): [string, string][] {
  const rows: [string, string][] = [];
  if (typeof args.north_m === 'number' && typeof args.east_m === 'number')
    rows.push(['Position', formatPosition(args.north_m, args.east_m)]);
  const units: Record<string, [string, string]> = {
    altitude_above_home_m: ['Altitude above home', 'm'],
    altitude_agl_m: ['Altitude above ground', 'm'],
    height_m: ['Height above ground', 'm'],
    zoom: ['Camera zoom', '×'],
    hold_seconds: ['Hold duration', 's'],
    speed_mps: ['Ground speed', 'm/s'],
    north_m: ['North', 'm'],
    east_m: ['East', 'm'],
    radius_m: ['Radius', 'm'],
  };
  for (const [key, value] of Object.entries(args)) {
    if ((key === 'north_m' || key === 'east_m') && rows.some(([label]) => label === 'Position')) continue;
    if (key === 'waypoints' && Array.isArray(value)) {
      value.forEach((point, i) =>
        rows.push([
          `Waypoint ${i + 1}`,
          argumentRows(point)
            .map(([label, value]) => `${label} ${value}`)
            .join(' · '),
        ]),
      );
    } else if (key in units && typeof value === 'number') {
      const [label, unit] = units[key];
      rows.push([label, formatValue(value, unit)]);
    } else
      rows.push([
        key === 'drone_id' ? 'Aircraft' : key.replaceAll('_', ' '),
        value !== null && typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value),
      ]);
  }
  return rows;
}

export function actionStatus(events: MissionEvent[]) {
  const result = events.filter((e) => e.kind === 'tool_result').at(-1);
  if (result)
    return result.result?.ok === true
      ? 'Done'
      : result.result?.error === 'Operator declined'
        ? 'Declined'
        : 'Failed';
  if (events.some((e) => e.kind === 'tool_running')) return 'Running';
  const approval = events.filter((e) => e.kind === 'approval').at(-1);
  if (approval?.result && 'approved' in approval.result)
    return approval.result.approved ? 'Approved' : 'Declined';
  return approval ? 'Awaiting approval' : 'Running';
}

export function flightLogText(event: MissionEvent) {
  if (event.name && event.arguments && typeof event.arguments.drone_id === 'string') {
    const modes: Record<string, string> = {
      take_off: 'Take off',
      fly_route: 'AUTO route',
      fly_to: 'GUIDED',
      hold: 'BRAKE',
      resume: 'AUTO resume',
      return_home: 'RTL',
      land: 'LAND',
    };
    const action = event.name in modes ? modes[event.name] : titles[event.name];
    let status = event.kind === 'tool_call' ? 'requested' : event.kind === 'tool_running' ? 'running' : '';
    if (event.kind === 'approval')
      status =
        event.result && 'approved' in event.result
          ? event.result.approved
            ? 'approved by operator'
            : 'declined by operator'
          : 'awaiting operator approval';
    if (event.kind === 'tool_result')
      status = event.result?.ok
        ? 'acknowledged'
        : event.result?.error === 'Operator declined'
          ? 'declined by operator'
          : String(event.result?.error);
    return `${event.arguments.drone_id} · ${action} ${status}`;
  }
  return event.text;
}
