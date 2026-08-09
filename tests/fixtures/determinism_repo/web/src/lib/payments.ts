export interface Charge {
  amount: number;
}

export function redirectToPayment(charge: Charge): string {
  return `/pay/${charge.amount}`;
}
