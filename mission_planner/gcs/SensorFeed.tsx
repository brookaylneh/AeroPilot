import { memo } from 'react';
import { Camera } from 'lucide-react';
import type { DroneId } from './droneIdentity';
import { formatPosition, formatValue } from './display';
import { recordSensorRender, type ReceivedSensorFrame } from './sensorFeedState';
import './sensorFeed.css';

function SensorFeed({
  droneId,
  frame,
  connected,
  inset,
  onError,
}: {
  droneId: DroneId;
  frame: ReceivedSensorFrame | undefined;
  connected: boolean;
  inset: boolean;
  onError: (message: string) => void;
}) {
  return (
    <section
      className={`gcs-video gcs-sensor ${inset ? 'is-inset' : ''}`}
      aria-label={`${droneId} sensor feed`}
      data-capture-id={frame?.capture_id}
    >
      {frame && (
        <img
          key={frame.capture_id}
          className="gcs-sensor-image"
          src={`data:image/jpeg;base64,${frame.preview.jpeg_base64}`}
          width={frame.preview.width}
          height={frame.preview.height}
          alt={`${droneId} nadir capture at ${frame.captured_at_utc}`}
          onLoad={() => recordSensorRender(frame)}
          onError={() => onError(`${droneId} sensor preview could not be decoded: ${frame.capture_id}`)}
        />
      )}
      <div className="gcs-video-top">
        <span>
          <Camera size={inset ? 11 : 14} />
          {droneId} · NADIR
        </span>
        <span>{connected ? (frame ? 'LAST CAPTURE' : 'WAITING') : 'DISCONNECTED'}</span>
      </div>
      {frame ? (
        <div className="gcs-sensor-caption">
          <time dateTime={frame.captured_at_utc}>
            {new Date(frame.captured_at_utc).toLocaleTimeString('en-GB', { hour12: false, timeZone: 'UTC' })}{' '}
            UTC
          </time>
          <span>{formatPosition(frame.pose.north_m, frame.pose.east_m)}</span>
          {!inset && (
            <span>
              {formatValue(frame.pose.altitude_msl_m, 'm')} MSL · Nadir error{' '}
              {formatValue(frame.pose.nadir_error_deg, '°', 2)}
            </span>
          )}
        </div>
      ) : (
        <div className="gcs-video-empty">
          <div className="gcs-video-icon">
            <Camera size={inset ? 24 : 32} strokeWidth={1.3} />
          </div>
          <strong>Waiting for first capture</strong>
          <p>{connected ? 'Sensor frames appear during AUTO flight.' : 'Mission service is disconnected.'}</p>
        </div>
      )}
    </section>
  );
}

export default memo(SensorFeed);
