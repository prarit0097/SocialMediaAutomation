import axios from "axios";

const META_GRAPH_BASE = "https://graph.facebook.com/v21.0";

export async function exchangeCodeForToken({ code, redirectUri, appId, appSecret }) {
  const { data } = await axios.get(`${META_GRAPH_BASE}/oauth/access_token`, {
    params: {
      client_id: appId,
      client_secret: appSecret,
      redirect_uri: redirectUri,
      code
    }
  });

  return data;
}

export async function getManagedPages(userAccessToken) {
  const { data } = await axios.get(`${META_GRAPH_BASE}/me/accounts`, {
    params: {
      access_token: userAccessToken,
      fields: "id,name,access_token,instagram_business_account"
    }
  });

  return data?.data || [];
}

export async function publishToFacebookPage({ pageId, pageAccessToken, message }) {
  const { data } = await axios.post(`${META_GRAPH_BASE}/${pageId}/feed`, null, {
    params: {
      access_token: pageAccessToken,
      message
    }
  });

  return data;
}

export async function createInstagramMediaContainer({ igUserId, pageAccessToken, imageUrl, caption }) {
  const { data } = await axios.post(`${META_GRAPH_BASE}/${igUserId}/media`, null, {
    params: {
      access_token: pageAccessToken,
      image_url: imageUrl,
      caption
    }
  });

  return data?.id;
}

export async function publishInstagramMedia({ igUserId, pageAccessToken, creationId }) {
  const { data } = await axios.post(`${META_GRAPH_BASE}/${igUserId}/media_publish`, null, {
    params: {
      access_token: pageAccessToken,
      creation_id: creationId
    }
  });

  return data;
}

export async function fetchFacebookInsights({ pageId, pageAccessToken }) {
  const { data } = await axios.get(`${META_GRAPH_BASE}/${pageId}/insights`, {
    params: {
      access_token: pageAccessToken,
      metric: "page_impressions,page_engaged_users,page_post_engagements",
      period: "day"
    }
  });

  return data?.data || [];
}

export async function fetchInstagramInsights({ igUserId, pageAccessToken }) {
  const { data } = await axios.get(`${META_GRAPH_BASE}/${igUserId}/insights`, {
    params: {
      access_token: pageAccessToken,
      metric: "impressions,reach,profile_views,website_clicks",
      period: "day"
    }
  });

  return data?.data || [];
}
