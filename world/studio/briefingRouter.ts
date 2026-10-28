import { Router } from 'express';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { z } from 'zod';
import { rescueCaseSchema } from './briefing';
import { apiError, HttpError, isMissingFile } from '../../app/http';

export function briefingRouter(directory: string) {
  const router = Router();
  router.get('/:id', async (req, res) => {
    const id = z.string().uuid().parse(req.params.id);
    let text: string;
    try {
      text = await readFile(path.join(directory, `${id}.json`), 'utf8');
    } catch (error) {
      if (isMissingFile(error)) throw new HttpError(404, 'Rescue briefing not found.');
      throw error;
    }
    res.setHeader('Cache-Control', 'no-store');
    res.json(rescueCaseSchema.parse(JSON.parse(text)));
  });
  router.use(apiError);
  return router;
}
