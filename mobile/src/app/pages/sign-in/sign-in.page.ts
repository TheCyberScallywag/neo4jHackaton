import { HttpErrorResponse } from '@angular/common/http';
import { Component, inject, signal } from '@angular/core';
import { FormControl, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router } from '@angular/router';
import {
  IonButton,
  IonButtons,
  IonContent,
  IonHeader,
  IonInput,
  IonItem,
  IonList,
  IonMenuButton,
  IonNote,
  IonText,
  IonTitle,
  IonToolbar,
} from '@ionic/angular';

import { SessionService } from '../../core/session.service';

@Component({
  selector: 'app-sign-in',
  templateUrl: 'sign-in.page.html',
  imports: [
    ReactiveFormsModule,
    IonButton,
    IonButtons,
    IonContent,
    IonHeader,
    IonInput,
    IonItem,
    IonList,
    IonMenuButton,
    IonNote,
    IonText,
    IonTitle,
    IonToolbar,
  ],
})
export class SignInPage {
  private readonly router = inject(Router);
  protected readonly session = inject(SessionService);
  readonly error = signal<string | null>(null);
  readonly busy = signal(false);

  readonly form = new FormGroup({
    email: new FormControl('', { nonNullable: true, validators: [Validators.required, Validators.email] }),
    password: new FormControl('', { nonNullable: true, validators: [Validators.required] }),
  });

  submit(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    const { email, password } = this.form.getRawValue();
    this.busy.set(true);
    this.error.set(null);
    this.session.signIn(email.trim(), password).subscribe({
      next: () => {
        this.busy.set(false);
        this.form.reset();
        void this.router.navigateByUrl('/matches');
      },
      error: (err: HttpErrorResponse) => {
        this.busy.set(false);
        this.error.set(err.status === 401 ? 'Wrong email or password' : err.status ? `Error ${err.status}` : 'Server unreachable');
      },
    });
  }
}
