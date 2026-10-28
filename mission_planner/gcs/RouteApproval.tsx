import { useEffect, useRef, useState } from 'react';
import { formatPosition, formatValue } from './display';
import type { RouteArguments } from './missionState';

export default function RouteApproval({ route }: { route: RouteArguments }) {
  const viewport = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState([0, 0]);
  function measure(el: HTMLDivElement) {
    const bounds = el.getBoundingClientRect();
    const headerHeight = el.querySelector('thead')!.getBoundingClientRect().height;
    const rows = [...el.querySelectorAll('tbody tr')];
    const indices = rows.flatMap((row, i) => {
      const box = row.getBoundingClientRect();
      return box.top >= bounds.top + headerHeight - 1 && box.bottom <= bounds.bottom + 1 ? [i + 1] : [];
    });
    setVisible([indices[0], indices.at(-1)!]);
  }
  useEffect(() => {
    const el = viewport.current!;
    const observer = new ResizeObserver(() => measure(el));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return (
    <section className="gcs-route-approval" aria-label="Requested route details">
      <div className="gcs-route-speed">
        <span>Ground speed</span>
        <strong>{formatValue(route.speed_mps, 'm/s')}</strong>
      </div>
      <div
        className="gcs-route-table"
        ref={viewport}
        onScroll={(event) => measure(event.currentTarget)}
        tabIndex={0}
        role="region"
        aria-label="Route waypoints. Scroll to review every waypoint."
      >
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Position from centre</th>
              <th>Above home</th>
            </tr>
          </thead>
          <tbody>
            {route.waypoints.map((point, i) => (
              <tr key={i} data-waypoint={i + 1}>
                <td>{i + 1}</td>
                <td>{formatPosition(point.north_m, point.east_m)}</td>
                <td>{formatValue(point.altitude_above_home_m, 'm')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="gcs-route-count">
        <span>
          Waypoints {visible[0]}–{visible[1]} of {route.waypoints.length}
        </span>
        <span>
          {visible[0] === 1 && visible[1] === route.waypoints.length ? 'All visible' : 'Scroll to review'}
        </span>
      </div>
    </section>
  );
}
