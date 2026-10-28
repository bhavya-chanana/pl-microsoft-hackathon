// The next-10-seconds companion's database, added to the EXISTING Cosmos DB account (store/next10.py).
//
// The deployed app owns the `matchlens` database in the same account (cosmos.bicep). This module only
// adds a separate `matchlens_next10` database and its containers; it does not redeclare the account,
// the app's database or its containers, so deploying it cannot change the running app. It is not part
// of main.bicep: `azd up` never deploys it. Deploy it on its own, after the owner's go-ahead:
//
//   az deployment group create -g <resource-group> -f infra/next10-cosmos.bicep -p accountName=<cosmos-account>
//
// Vector indexes need the account's vector-search capability, which is an account-level change; it is
// off by default (enableVectors=false) and is a separate decision.
@description('Name of the existing Cosmos DB account (the one cosmos.bicep created).')
param accountName string

@description('Principal id to grant Data Contributor on the next10 database only (optional).')
param principalId string = ''

@description('Add DiskANN vector indexes (requires vector search enabled on the account).')
param enableVectors bool = false

var databaseName = 'matchlens_next10'
var dataContributor = '00000000-0000-0000-0000-000000000002'

// Keep in sync with CONTAINERS in store/next10.py; tests/test_next10_store.py checks it.
var containers = [
  { name: 'matches', partition: 'match_id', unindexed: ['sources', 'lineups'], vector: '', dims: 0 }
  { name: 'events', partition: 'match_id', unindexed: ['attrs', 'start', 'end'], vector: '', dims: 0 }
  { name: 'frames', partition: 'match_id', unindexed: ['entities'], vector: '', dims: 0 }
  { name: 'predictions', partition: 'match_id', unindexed: ['features', 'probs'], vector: '', dims: 0 }
  { name: 'moments', partition: 'match_id', unindexed: ['graph'], vector: 'embedding', dims: 64 }
  { name: 'video_jobs', partition: 'job_id', unindexed: ['chunks', 'calibration'], vector: '', dims: 0 }
  { name: 'kg_nodes', partition: 'type', unindexed: ['props'], vector: 'text_embedding', dims: 1536 }
  { name: 'kg_edges', partition: 'src', unindexed: ['props'], vector: '', dims: 0 }
]

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' existing = {
  name: accountName
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
      resource: union(
        {
          id: c.name
          partitionKey: { paths: ['/${c.partition}'], kind: 'Hash', version: 2 }
          indexingPolicy: union(
            {
              indexingMode: 'consistent'
              automatic: true
              includedPaths: [{ path: '/*' }]
              excludedPaths: concat(
                map(c.unindexed, p => { path: '/${p}/*' }),
                [{ path: '/"_etag"/?' }],
                empty(c.vector) ? [] : [{ path: '/${c.vector}/*' }]
              )
            },
            enableVectors && !empty(c.vector) ? { vectorIndexes: [{ path: '/${c.vector}', type: 'diskANN' }] } : {}
          )
        },
        enableVectors && !empty(c.vector)
          ? {
              vectorEmbeddingPolicy: {
                vectorEmbeddings: [
                  { path: '/${c.vector}', dataType: 'float32', distanceFunction: 'cosine', dimensions: c.dims }
                ]
              }
            }
          : {}
      )
    }
  }
]

// Scoped to the next10 database, not the account, so this identity cannot write the app's data.
resource dataAccess 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = if (!empty(principalId)) {
  parent: account
  name: guid(account.id, databaseName, principalId, dataContributor)
  properties: {
    roleDefinitionId: '${account.id}/sqlRoleDefinitions/${dataContributor}'
    principalId: principalId
    scope: '${account.id}/dbs/${databaseName}'
  }
}
