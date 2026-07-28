import { redirectToPayment } from '../../lib/payments'
export function useCheckout() { return { checkout: redirectToPayment } }
