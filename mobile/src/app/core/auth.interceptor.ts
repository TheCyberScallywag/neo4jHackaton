import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';

import { environment } from '../../environments/environment';
import { SessionService } from './session.service';

/**
 * Sends the bearer token to the API (and only the API). A 401 on a signed-in request means the
 * session ended or was revoked, so it forgets the token and goes to sign-in.
 */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const session = inject(SessionService);
  const router = inject(Router);
  const token = session.token();
  const toApi = req.url.startsWith(environment.apiUrl);
  const request = token && toApi ? req.clone({ setHeaders: { Authorization: `Bearer ${token}` } }) : req;
  return next(request).pipe(
    catchError((err: unknown) => {
      if (err instanceof HttpErrorResponse && err.status === 401 && token && toApi) {
        session.forget();
        void router.navigateByUrl('/sign-in');
      }
      return throwError(() => err);
    }),
  );
};
