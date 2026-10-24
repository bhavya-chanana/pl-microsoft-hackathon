// Container Apps environment, registry, identity and the two Matchlens apps.
param environmentName string
param location string
param tags object
param openAiEndpoint string
param modelFast string
param modelStrong string
param reasoningFast string
param reasoningStrong string
param backendMinReplicas int
param matchSpeed string
param backendImageName string
param frontendImageName string

var token = toLower(uniqueString(subscription().id, environmentName, location))
var placeholderImage = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
var acrPullRole = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${token}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'cr${token}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-matchlens-${token}'
  location: location
  tags: tags
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, acrPullRole)
  scope: registry
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRole)
  }
}

// The store: every run, insight, overlay and agent decision (Cosmos DB serverless).
module cosmos 'cosmos.bicep' = {
  name: 'matchlens-cosmos'
  params: {
    location: location
    tags: tags
    token: token
    principalId: identity.properties.principalId
  }
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${token}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

var identityRef = { type: 'UserAssigned', userAssignedIdentities: { '${identity.id}': {} } }
var registries = [{ server: registry.properties.loginServer, identity: identity.id }]

resource backend 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-backend-${token}'
  location: location
  tags: union(tags, { 'azd-service-name': 'backend' })
  identity: identityRef
  dependsOn: [acrPull]
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      activeRevisionsMode: 'Single'
      // Internal only: the browser reaches it through the frontend's allow-listed proxy.
      ingress: {
        external: false
        targetPort: 8765
        transport: 'auto'
      }
      registries: registries
    }
    template: {
      containers: [
        {
          name: 'backend'
          image: !empty(backendImageName) ? backendImageName : placeholderImage
          resources: { cpu: json('1.0'), memory: '2Gi' }
          env: [
            { name: 'AZURE_OPENAI_ENDPOINT', value: openAiEndpoint }
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
            { name: 'MATCHLENS_LLM', value: 'auto' }
            { name: 'MATCHLENS_MODEL_FAST', value: modelFast }
            { name: 'MATCHLENS_MODEL_STRONG', value: modelStrong }
            { name: 'MATCHLENS_REASONING_FAST', value: reasoningFast }
            { name: 'MATCHLENS_REASONING_STRONG', value: reasoningStrong }
            { name: 'MATCHLENS_SPEED', value: matchSpeed }
            { name: 'MATCHLENS_COSMOS_ENDPOINT', value: cosmos.outputs.endpoint }
            { name: 'MATCHLENS_COSMOS_DATABASE', value: cosmos.outputs.databaseName }
          ]
          probes: [
            {
              type: 'Readiness'
              httpGet: { path: '/api/health', port: 8765 }
              initialDelaySeconds: 3
              periodSeconds: 10
            }
          ]
        }
      ]
      // One live match per process (its state is stored in Cosmos DB), so never more than one replica.
      scale: {
        minReplicas: backendMinReplicas
        maxReplicas: 1
        rules: [{ name: 'http', http: { metadata: { concurrentRequests: '200' } } }]
      }
    }
  }
}

var backendUrl = 'https://${backend.properties.configuration.ingress.fqdn}'

resource frontend 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-frontend-${token}'
  location: location
  tags: union(tags, { 'azd-service-name': 'frontend' })
  identity: identityRef
  dependsOn: [acrPull]
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 3000
        transport: 'auto'
        allowInsecure: false
      }
      registries: registries
    }
    template: {
      containers: [
        {
          name: 'frontend'
          image: !empty(frontendImageName) ? frontendImageName : placeholderImage
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'MATCHLENS_BACKEND_URL', value: backendUrl }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 3
        rules: [{ name: 'http', http: { metadata: { concurrentRequests: '100' } } }]
      }
    }
  }
}

output identityPrincipalId string = identity.properties.principalId
output registryLoginServer string = registry.properties.loginServer
output registryName string = registry.name
output frontendUrl string = 'https://${frontend.properties.configuration.ingress.fqdn}'
output backendUrl string = backendUrl
output cosmosEndpoint string = cosmos.outputs.endpoint
