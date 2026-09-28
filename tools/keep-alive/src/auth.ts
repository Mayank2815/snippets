import type { NextFunction, Request, Response } from 'express';
import { timingSafeEqual } from 'node:crypto';

/**
 * Optional HTTP Basic auth for the dashboard.
 *
 * Locally the port is bound to loopback, so this is unnecessary. On any host where the
 * port is public a password is required, not optional: the dashboard can press Start on
 * someone's server, and lists every internal hostname it knows about.
 */
export function basicAuth(password: string) {
  const expected = Buffer.from(password);

  return (req: Request, res: Response, next: NextFunction): void => {
    const header = req.headers.authorization ?? '';
    const [scheme, encoded] = header.split(' ');

    // Scheme names are case-insensitive (RFC 7235); curl and browsers send "Basic", but not every client does.
    if (scheme?.toLowerCase() === 'basic' && encoded) {
      const supplied = Buffer.from(Buffer.from(encoded, 'base64').toString('utf8').split(':').slice(1).join(':'));
      // Constant-time so a wrong password cannot be guessed a character at a time.
      if (supplied.length === expected.length && timingSafeEqual(supplied, expected)) {
        next();
        return;
      }
    }

    res.set('WWW-Authenticate', 'Basic realm="Keep Alive", charset="UTF-8"');
    res.status(401).send('Authentication required');
  };
}
