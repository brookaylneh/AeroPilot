import type { ErrorRequestHandler } from 'express';
import { z } from 'zod';

export class HttpError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export function isMissingFile(error: unknown): boolean {
  return error instanceof Error && 'code' in error && error.code === 'ENOENT';
}

export const apiError: ErrorRequestHandler = (error: unknown, _req, res, _next) => {
  if (error instanceof HttpError) {
    res.status(error.status).json({ error: error.message });
  } else if (error instanceof z.ZodError || (error instanceof SyntaxError && 'body' in error)) {
    res.status(400).json({ error: 'Invalid request data.' });
  } else if (error instanceof Error && 'type' in error && error.type === 'entity.too.large') {
    res.status(413).json({ error: 'Request body is too large.' });
  } else {
    console.error('API failure:', error);
    res.status(500).json({ error: 'Internal server error. See the server log.' });
  }
};
