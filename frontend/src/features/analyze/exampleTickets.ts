export interface ExampleTicket {
  id: string
  label: string
  description: string
}

/**
 * Illustrative tickets written for this console. They are not customer data;
 * they cover the three outcomes the agent can return.
 */
export const EXAMPLE_TICKETS: ReadonlyArray<ExampleTicket> = [
  {
    id: 'database-timeout',
    label: 'Database connection timeouts',
    description:
      'Since this morning the orders service logs "connection timed out" when opening connections to PostgreSQL. ' +
      'Around a third of requests fail, and the connection pool reports all connections in use.',
  },
  {
    id: 'checkout-504',
    label: 'HTTP 504 on checkout',
    description:
      'Customers get an HTTP 504 Gateway Timeout when submitting the checkout form. ' +
      'The load balancer logs show the upstream taking longer than 60 seconds to respond.',
  },
  {
    id: 'dns-migration',
    label: 'Hostname not resolving after migration',
    description:
      'After moving the reporting server to the new subnet, clients fail to reach reports.internal. ' +
      'nslookup returns NXDOMAIN, but the server answers when we use its IP address directly.',
  },
  {
    id: 'vague-report',
    label: 'Vague report with no symptom',
    description: 'It stopped working after yesterday.',
  },
  {
    id: 'outside-docs',
    label: 'Topic outside the documentation',
    description:
      'Our Terraform apply fails with "Error acquiring the state lock" on the shared S3 backend. ' +
      'No other pipeline seems to be running.',
  },
]
