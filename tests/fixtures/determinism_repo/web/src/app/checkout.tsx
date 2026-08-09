import { redirectToPayment } from "../lib/payments";

export default function Checkout() {
  return redirectToPayment({ amount: 1 });
}
