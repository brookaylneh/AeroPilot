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

export function formatProbability(value: number) {
  if (!Number.isFinite(value) || value < 0 || value > 1) throw new Error('Invalid probability');
  const percent = value * 100;
  return `${percent.toLocaleString('en-US', { maximumSignificantDigits: 3, useGrouping: false })}%`;
}

const titles: Record<string, string> = {
  get_mission: 'Mission briefing',
  get_fleet: 'Fleet status',
  get_drone: 'Aircraft status',
  show_on_map: 'Focus map',
  take_off: 'Take off',
  fly_route: 'Fly route',
  fly_to: 'Fly to',
  hold: 'Hold',
  resume: 'Resume',
  return_home: 'Return home',
  land: 'Land',
  record_subject_profile: 'Subject profile',
  find_place: 'Find place',
  describe_location: 'Landmark directions',
  set_last_known_place: 'Last known place',
  define_search_area: 'Search area',
  plan_search: 'Plan search',
  start_search: 'Fleet search',
  get_probability_map: 'Probability map',
  get_coverage: 'Camera coverage',
  list_detections: 'List evidence',
  review_detection: 'Review evidence',
  resolve_detection: 'Resolve evidence',
  relook_detection: 'Re-look at evidence',
  notify_ground_team: 'Deliver ground-team lead',
  hasty_check_structure: 'Request structure check',
  confirm_structure_negative: 'Confirm negative structure check',
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
  if (name === 'set_last_known_place') return 'Set the last known location from the highlighted place';
  if (name === 'notify_ground_team') return `Deliver the confirmed lead to ground team ${args.team_id}`;
  if (name === 'hasty_check_structure')
    return `Ask ground team ${args.team_id} to check the highlighted structure`;
  if (name === 'relook_detection')
    return `Fly ${args.drone_id} to the evidence location at ${args.altitude_agl_m} m above ground and hold for ${args.hold_seconds} s`;
  if (name === 'start_search') return 'Fly the approved search with all four aircraft';
  if (name === 'take_off' && typeof args.altitude_above_home_m === 'number')
    return `Take off ${args.drone_id} to ${formatValue(args.altitude_above_home_m, 'm', 0)} above home`;
  if (name === 'fly_route' && Array.isArray(args.waypoints))
    return `Fly ${args.drone_id} through ${waypointCount(args.waypoints.length)}`;
  if (name === 'fly_to') return `Fly ${args.drone_id} to the selected destination`;
  if (name === 'return_home') return `Return ${args.drone_id} home and land`;
  if (name === 'land') return `Land ${args.drone_id} at its current position`;
  if (name === 'hold') return `Brake and hold ${args.drone_id} in place`;
  return `Resume ${args.drone_id}'s uploaded mission`;
}

export function argumentRows(args: Record<string, unknown>): [string, string][] {
  const rows: [string, string][] = [];
  if (typeof args.north_m === 'number' && typeof args.east_m === 'number')
    rows.push(['Position', formatPosition(args.north_m, args.east_m)]);
  const units: Record<string, [string, string]> = {
    altitude_above_home_m: ['Altitude above home', 'm'],
    altitude_agl_m: ['Altitude above ground', 'm'],
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
    } else rows.push([key === 'drone_id' ? 'Aircraft' : key.replaceAll('_', ' '), String(value)]);
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
