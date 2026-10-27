import type { RequestHandler, ErrorRequestHandler } from 'express';
import { z } from 'zod';

export class HttpError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export const sameOrigin: RequestHandler = (req, res, next) => {
  const origin = req.get('origin');
  if (origin) {
    let url: URL;
    try {
      url = new URL(origin);
    } catch {
      next(new HttpError(400, 'Invalid origin.'));
      return;
    }
    if (!['http:', 'https:'].includes(url.protocol) || url.host !== req.get('host')) {
      next(new HttpError(403, 'Cross-origin access is not allowed.'));
      return;
    }
  }
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('X-Content-Type-Options', 'nosniff');
  next();
};

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
