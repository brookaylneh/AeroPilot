export const droneIds = ['Drone1', 'Drone2', 'Drone3', 'Drone4'] as const;
export type DroneId = (typeof droneIds)[number];
export const droneColors: Record<DroneId, string> = {
  Drone1: '#56b4e9',
  Drone2: '#e6b450',
  Drone3: '#cc79a7',
  Drone4: '#68d6b1',
};
