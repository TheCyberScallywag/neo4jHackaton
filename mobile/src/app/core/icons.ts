import { addIcons } from 'ionicons';
import {
  documentTextOutline,
  handLeftOutline,
  logInOutline,
  logOutOutline,
  notificationsOutline,
  peopleOutline,
  personCircleOutline,
  sparklesOutline,
} from 'ionicons/icons';

// Standalone Ionic only renders registered icons.
export function registerIcons(): void {
  addIcons({
    'document-text-outline': documentTextOutline,
    'hand-left-outline': handLeftOutline,
    'notifications-outline': notificationsOutline,
    'log-in-outline': logInOutline,
    'log-out-outline': logOutOutline,
    'people-outline': peopleOutline,
    'person-circle-outline': personCircleOutline,
    'sparkles-outline': sparklesOutline,
  });
}
