// Azure Cosmos DB for NoSQL, serverless: the Matchlens store (backend/src/matchlens/store/base.py).
//
// Serverless bills per request unit and GB stored, so an idle demo costs nothing. Key auth is
// disabled: the backend's managed identity gets the built-in Data Contributor role (read and
// write items). That role cannot create containers, so they are declared here.
param location string
param tags object
param token string
@description('Principal id of the backend\'s managed identity.')
param principalId string

var databaseName = 'matchlens'
var dataContributor = '00000000-0000-0000-0000-000000000002'

// Keep in sync with CONTAINERS in store/base.py; tests/test_store.py checks it.
// Every container is partitioned by /run_id; unindexed fields are never filtered on.
var containers = [
  { name: 'runs', unindexed: ['meta', 'state', 'coordinator', 'scheduler', 'teams', 'players'] }
  { name: 'events', unindexed: ['attrs', 'start', 'end', 'pass_quality'] }
  { name: 'frames', unindexed: ['entities'] }
  { name: 'signals', unindexed: ['facts', 'evidence_event_ids'] }
  { name: 'insights', unindexed: ['variants', 'club_angles', 'fact_check', 'key_numbers', 'evidence_event_ids'] }
  { name: 'overlays', unindexed: ['localized', 'stats'] }
  { name: 'decisions', unindexed: [] }
  { name: 'agent_runs', unindexed: ['errors', 'actions'] }
  { name: 'fan_outputs', unindexed: ['result'] }
  { name: 'fan_votes', unindexed: [] }
]

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: 'cosmos-${token}'
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    locations: [{ locationName: location, failoverPriority: 0, isZoneRedundant: false }]
    capabilities: [{ name: 'EnableServerless' }]
    consistencyPolicy: { defaultConsistencyLevel: 'Session' }
    disableLocalAuth: true
    minimalTlsVersion: 'Tls12'
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' = {
  parent: account
  name: databaseName
  properties: { resource: { id: databaseName } }
}

resource container 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = [
  for c in containers: {
    parent: database
    name: c.name
    properties: {
      resource: {
        id: c.name
        partitionKey: { paths: ['/run_id'], kind: 'Hash', version: 2 }
        indexingPolicy: {
          indexingMode: 'consistent'
          automatic: true
          includedPaths: [{ path: '/*' }]
          excludedPaths: concat(map(c.unindexed, p => { path: '/${p}/*' }), [{ path: '/"_etag"/?' }])
        }
      }
    }
  }
]

resource dataAccess 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = {
  parent: account
  name: guid(account.id, principalId, dataContributor)
  properties: {
    principalId: principalId
    roleDefinitionId: '${account.id}/sqlRoleDefinitions/${dataContributor}'
    scope: account.id
  }
}

output endpoint string = account.properties.documentEndpoint
output databaseName string = databaseName
