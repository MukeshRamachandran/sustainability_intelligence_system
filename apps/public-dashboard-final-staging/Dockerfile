FROM nginx:1.27-alpine

COPY . /usr/share/nginx/html/

RUN rm -rf \
    /usr/share/nginx/html/.agents \
    /usr/share/nginx/html/.claude \
    /usr/share/nginx/html/tests \
    /usr/share/nginx/html/serve-staging.py \
    /usr/share/nginx/html/skills-lock.json \
    /usr/share/nginx/html/Dockerfile

EXPOSE 80
