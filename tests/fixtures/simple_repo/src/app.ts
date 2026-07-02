import { createSession } from './auth/session'
export function loginHandler() { return createSession('u1') }
