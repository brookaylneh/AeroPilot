import assert from 'node:assert/strict';

export function required(name: string): string {
  const value = process.env[name];
  assert(value, `Missing ${name}; load .local/runtime.env`);
  return value;
}

export function port(name: string): number {
  const value = Number(required(name));
  assert(Number.isInteger(value) && value > 0 && value < 65536, `Invalid ${name}`);
  return value;
}
