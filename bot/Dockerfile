FROM node:20-slim

WORKDIR /app

COPY package.json .
RUN npm install

COPY . .

# Create directory for auth persistence
RUN mkdir -p auth_info

CMD ["node", "index.js"]
